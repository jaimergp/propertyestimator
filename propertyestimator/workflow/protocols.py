"""
A collection of specialized workflow building blocks, which when chained together,
form a larger property estimation workflow.
"""

import copy
import logging
import math
import pickle
from os import path

import mdtraj
import numpy as np
from simtk import openmm, unit
from simtk.openmm import app, System, Platform

from propertyestimator.substances import Substance
from propertyestimator.thermodynamics import ThermodynamicState, Ensemble
from propertyestimator.utils import packmol, graph, utils, timeseries, create_molecule_from_smiles
from propertyestimator.utils.exceptions import PropertyEstimatorException
from propertyestimator.utils.serialization import serialize_quantity, deserialize_quantity, PolymorphicDataType
from propertyestimator.utils.statistics import Statistics, AvailableQuantities
from propertyestimator.workflow.decorators import protocol_input, protocol_output, MergeBehaviour
from propertyestimator.workflow.plugins import register_calculation_protocol
from propertyestimator.workflow.schemas import ProtocolSchema
from propertyestimator.workflow.utils import ProtocolPath


class BaseProtocol:
    """The base class for a protocol which would form one
    step of a larger property calculation workflow.

    A protocol may for example:

        * create the coordinates of a mixed simulation box
        * set up a bound ligand-protein system
        * build the simulation topology
        * perform an energy minimisation

    An individual protocol may require a set of inputs, which may either be
    set as constants

    >>> npt_equilibration = RunOpenMMSimulation('npt_equilibration')
    >>> npt_equilibration.ensemble = RunOpenMMSimulation.Ensemble.NPT

    or from the output of another protocol, pointed to by a ProtocolPath

    >>> npt_production = RunOpenMMSimulation('npt_production')
    >>> # Use the coordinate file output by the npt_equilibration protocol
    >>> # as the input to the npt_production protocol
    >>> npt_production.input_coordinate_file = ProtocolPath('output_coordinate_file',
    >>>                                                     npt_equilibration.id)

    In this way protocols may be chained together, thus defining a larger property
    calculation workflow from simple, reusable building blocks.

    .. warning:: This class is still heavily under development and is subject to
                 rapid changes.
    """

    @property
    def id(self):
        """str: The unique id of this protocol."""
        return self._id

    @property
    def schema(self):
        """ProtocolSchema: A serializable schema for this object."""
        return self._get_schema()

    @schema.setter
    def schema(self, schema_value):
        self._set_schema(schema_value)

    @property
    def dependencies(self):
        """list of ProtocolPath: A list of pointers to the protocols which this
        protocol takes input from.
        """

        return_dependencies = []

        for input_path in self.required_inputs:

            input_value = self.get_value(input_path)

            if not isinstance(input_value, ProtocolPath):
                continue

            if input_value not in return_dependencies:
                return_dependencies.append(input_value)

        return return_dependencies

    @protocol_input(value_type=bool)
    def allow_merging(self):
        """bool: If true, this protocol is allowed to merge with other identical protocols."""
        pass

    def __init__(self, protocol_id):

        # A unique identifier for this node.
        self._id = protocol_id

        # Defines whether a protocol is allowed to try and merge with other identical ones.
        self._allow_merging = True

        # Find the required inputs and outputs.
        self.provided_outputs = []
        self.required_inputs = []

        output_attributes = utils.find_types_with_decorator(type(self), 'ProtocolOutputObject')
        input_attributes = utils.find_types_with_decorator(type(self), 'ProtocolInputObject')

        for output_attribute in output_attributes:
            self.provided_outputs.append(ProtocolPath(output_attribute))

        for input_attribute in input_attributes:
            self.required_inputs.append(ProtocolPath(input_attribute))

        # The directory in which to execute the protocol.
        self.directory = None

    def execute(self, directory, available_resources):
        """ Execute the protocol.

        Protocols may be chained together by passing the output
        of previous protocols as input to the current one.

        Parameters
        ----------
        directory: str
            The directory to store output data in.
        available_resources: PropertyEstimatorBackendResources
            The resources available to execute on.

        Returns
        ----------
        Dict[str, Any]
            The output of the execution.
        """

        return self._get_output_dictionary()

    def _get_schema(self):
        """Returns this protocols properties (i.e id and parameters)
        as a ProtocolSchema

        Returns
        -------
        ProtocolSchema
            The schema representation.
        """

        schema = ProtocolSchema()

        schema.id = self.id
        schema.type = type(self).__name__

        for input_path in self.required_inputs:

            if not (input_path.start_protocol is None or (input_path.start_protocol == self.id and
                                                          input_path.start_protocol == input_path.last_protocol)):

                continue

            value = self.get_value(input_path)

            if isinstance(value, unit.Quantity):
                value = serialize_quantity(value)

            schema.inputs[input_path.full_path] = PolymorphicDataType(value)

        return schema

    def _set_schema(self, schema_value):
        """Sets this protocols properties (i.e id and parameters)
        from a ProtocolSchema

        Parameters
        ----------
        schema_value: ProtocolSchema
            The schema which will describe this protocol.
        """
        self._id = schema_value.id

        if type(self).__name__ != schema_value.type:
            # Make sure this object is the correct type.
            raise ValueError('Cannot convert a {} protocol to a {}.'
                             .format(str(type(self)), schema_value.type))

        for input_full_path in schema_value.inputs:

            value = schema_value.inputs[input_full_path].value

            if isinstance(value, dict) and 'unit' in value and 'unitless_value' in value:
                value = deserialize_quantity(value)

            input_path = ProtocolPath.from_string(input_full_path)
            self.set_value(input_path, value)

    def _get_output_dictionary(self):
        """Builds a dictionary of the output property names and their values.

        Returns
        -------
        Dict[str, Any]
            A dictionary whose keys are the output property names, and the
            values their associated values.
        """

        return_dictionary = {}

        for output_path in self.provided_outputs:
            return_dictionary[output_path.full_path] = self.get_value(output_path)

        return return_dictionary

    def set_uuid(self, value):
        """Store the uuid of the calculation this protocol belongs to

        Parameters
        ----------
        value : str
            The uuid of the parent calculation.
        """
        if self.id.find(value) >= 0:
            return

        self._id = graph.append_uuid(self.id, value)

        for input_path in self.required_inputs:

            input_path.append_uuid(value)
            input_value = self.get_value(input_path)

            if isinstance(input_value, ProtocolPath):
                input_value.append_uuid(value)

        for output_path in self.provided_outputs:
            output_path.append_uuid(value)

    def replace_protocol(self, old_id, new_id):
        """Finds each input which came from a given protocol
         and redirects it to instead take input from a new one.

        Notes
        -----
        This method is mainly intended to be used only when merging
        multiple protocols into one.

        Parameters
        ----------
        old_id : str
            The id of the old input protocol.
        new_id : str
            The id of the new input protocol.
        """

        for input_path in self.required_inputs:

            input_path.replace_protocol(old_id, new_id)

            if input_path.start_protocol is not None or (input_path.start_protocol != input_path.last_protocol and
                                                         input_path.start_protocol != self.id):
                continue

            input_value = self.get_value(input_path)

            if isinstance(input_value, ProtocolPath):
                input_value.replace_protocol(old_id, new_id)

        for output_path in self.provided_outputs:
            output_path.replace_protocol(old_id, new_id)

    def can_merge(self, other):
        """Determines whether this protocol can be merged with another.

        Parameters
        ----------
        other : :obj:`BaseProtocol`
            The protocol to compare against.

        Returns
        ----------
        bool
            True if the two protocols are safe to merge.
        """
        if not self.allow_merging:
            return False

        if not isinstance(self, type(other)):
            return False

        for input_path in self.required_inputs:

            if input_path.start_protocol is not None and input_path.start_protocol != self.id:
                continue

            # Do not consider paths that point to child (e.g grouped) protocols.
            # These should be handled by the container classes themselves.
            if not (input_path.start_protocol is None or (
                    input_path.start_protocol == input_path.last_protocol and
                    input_path.start_protocol == self.id)):

                continue

            # If no merge behaviour flag is present (for example in the case of
            # ConditionalGroup conditions), simply assume this is handled explicitly
            # elsewhere.
            if not hasattr(getattr(type(self), input_path.property_name), 'merge_behavior'):
                continue

            merge_behavior = getattr(type(self), input_path.property_name).merge_behavior

            if merge_behavior != MergeBehaviour.ExactlyEqual:
                continue

            if input_path not in other.required_inputs:
                return False

            self_value = self.get_value(input_path)
            other_value = other.get_value(input_path)

            if self_value != other_value:
                return False

        return True

    def merge(self, other):
        """Merges another BaseProtocol with this one. The id
        of this protocol will remain unchanged.

        It is assumed that can_merge has already returned that
        these protocols are compatible to be merged together.

        Parameters
        ----------
        other: BaseProtocol
            The protocol to merge into this one.

        Returns
        -------
        Dict[str, str]
            A map between any original protocol ids and their new merged values.
        """

        for input_path in self.required_inputs:

            # Do not consider paths that point to child (e.g grouped) protocols.
            # These should be handled by the container classes themselves.
            if not (input_path.start_protocol is None or (
                    input_path.start_protocol == input_path.last_protocol and
                    input_path.start_protocol == self.id)):

                continue

            # If no merge behaviour flag is present (for example in the case of
            # ConditionalGroup conditions), simply assume this is handled explicitly
            # elsewhere.
            if not hasattr(getattr(type(self), input_path.property_name), 'merge_behavior'):
                continue

            merge_behavior = getattr(type(self), input_path.property_name).merge_behavior

            if merge_behavior == MergeBehaviour.ExactlyEqual:
                continue

            value = None

            if merge_behavior == MergeBehaviour.SmallestValue:
                value = min(self.get_value(input_path), other.get_value(input_path))
            elif merge_behavior == MergeBehaviour.GreatestValue:
                value = max(self.get_value(input_path), other.get_value(input_path))

            self.set_value(input_path, value)

        return {}

    def get_attribute_type(self, reference_path):
        """Returns the type of one of the protocol input/output attributes.

        Parameters
        ----------
        reference_path: ProtocolPath
            The path pointing to the value whose type to return.

        Returns
        ----------
        type:
            The type of the attribute.
        """

        if reference_path.start_protocol is not None and reference_path.start_protocol != self.id:
            raise ValueError('The reference path {} does not point to this protocol'.format(reference_path))

        return getattr(type(self), reference_path.property_name).value_type

    def get_value(self, reference_path):
        """Returns the value of one of this protocols parameters / inputs.

        Parameters
        ----------
        reference_path: ProtocolPath
            The path pointing to the value to return.

        Returns
        ----------
        object:
            The value of the input
        """

        if (reference_path.start_protocol is not None and
            reference_path.start_protocol != self.id):

            raise ValueError('The reference path does not target this protocol.')

        if not hasattr(self, reference_path.property_name):

            raise ValueError('This protocol does not have contain a {} '
                             'property.'.format(reference_path.property_name))

        return getattr(self, reference_path.property_name)

    def set_value(self, reference_path, value):
        """Sets the value of one of this protocols parameters / inputs.

        Parameters
        ----------
        reference_path: ProtocolPath
            The path pointing to the value to return.
        value: Any
            The value to set.
        """

        if (reference_path.start_protocol is not None and
            reference_path.start_protocol != self.id):

            raise ValueError('The reference path does not target this protocol.')

        if not hasattr(self, reference_path.property_name):

            raise ValueError('This protocol does not have contain a {} '
                             'property.'.format(reference_path.property_name))

        if reference_path in self.provided_outputs:
            raise ValueError('Output values cannot be set by this method.')

        setattr(self, reference_path.property_name, value)


@register_calculation_protocol()
class BuildCoordinatesPackmol(BaseProtocol):
    """Creates a set of 3D coordinates with a specified composition.

    Notes
    -----
    The coordinates are created using packmol.
    """

    @protocol_input(int)
    def max_molecules(self):
        """The maximum number of molecules to be added to the system."""
        pass

    @protocol_input(unit.Quantity)
    def mass_density(self):
        """The target density of the created system."""
        pass

    @protocol_input(Substance)
    def substance(self):
        """The composition of the system to build."""
        pass

    @protocol_output(str)
    def coordinate_file_path(self):
        """The file path to the created PDB coordinate file."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        # inputs
        self._substance = None

        # outputs
        self._coordinate_file_path = None
        self._positions = None

        self._max_molecules = 128
        self._mass_density = 1.0 * unit.grams / unit.milliliters

    def execute(self, directory, available_resources):

        logging.info('Generating coordinates: ' + self.id)

        if self._substance is None:

            return PropertyEstimatorException(directory=directory,
                                              message='The substance input is non-optional')

        molecules = []

        for component in self._substance.components:

            molecule = create_molecule_from_smiles(component.smiles)

            if molecule is None:

                return PropertyEstimatorException(directory=directory,
                                                  message='{} could not be converted to a Molecule'.format(component))

            molecules.append(molecule)

        # Determine how many molecules of each type will be present in the system.
        mole_fractions = np.array([component.mole_fraction for component in self._substance.components])

        n_copies = np.random.multinomial(self._max_molecules - self._substance.number_of_impurities,
                                         pvals=mole_fractions)

        # Each impurity must have exactly one molecule
        for (index, component) in enumerate(self._substance.components):

            if component.impurity:
                n_copies[index] = 1

        # Create packed box
        topology, positions = packmol.pack_box(molecules, n_copies, mass_density=self._mass_density)

        if topology is None or positions is None:

            return PropertyEstimatorException(directory=directory,
                                              message='Packmol failed to complete.')

        self._coordinate_file_path = path.join(directory, 'output.pdb')

        with open(self._coordinate_file_path, 'w+') as minimised_file:
            app.PDBFile.writeFile(topology, positions, minimised_file)

        logging.info('Coordinates generated: ' + str(self._substance))

        return self._get_output_dictionary()


@register_calculation_protocol()
class BuildSmirnoffTopology(BaseProtocol):
    """Parametrise a set of molecules with a given smirnoff force field.
    """

    @protocol_input(str)
    def force_field_path(self, value):
        """The file path to the force field parameters to assign to the system."""
        pass

    @protocol_input(str)
    def coordinate_file_path(self, value):
        """The file path to the coordinate file which defines the system to which the
        force field parameters will be assigned."""
        pass

    @protocol_input(Substance)
    def substance(self):
        """The composition of the system."""
        pass

    @protocol_output(System)
    def system(self):
        """The assigned system."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        # inputs
        self._force_field_path = None
        self._coordinate_file_path = None
        self._substance = None

        # outputs
        self._system = None

    def execute(self, directory, available_resources):

        logging.info('Generating topology: ' + self.id)

        pdb_file = app.PDBFile(self._coordinate_file_path)

        parameter_set = None

        with open(self._force_field_path, 'rb') as file:
            parameter_set = pickle.load(file)

        molecules = []

        for component in self._substance.components:

            molecule = create_molecule_from_smiles(component.smiles)

            if molecule is None:
                return PropertyEstimatorException(directory=directory,
                                                  message='{} could not be converted to a Molecule'.format(component))

            molecules.append(molecule)

        from openforcefield.typing.engines import smirnoff

        system = parameter_set.createSystem(pdb_file.topology,
                                            molecules,
                                            nonbondedMethod=smirnoff.PME,
                                            chargeMethod='OECharges_AM1BCCSym')

        if system is None:

            return PropertyEstimatorException(directory=directory,
                                              message='Failed to create a system from the'
                                                       'provided topology and molecules')

        self._system = system

        logging.info('Topology generated: ' + self.id)

        return self._get_output_dictionary()


@register_calculation_protocol()
class RunEnergyMinimisation(BaseProtocol):
    """A protocol to minimise the potential energy of a system.

    .. todo:: Add arguments for max iterations + tolerance
    """

    @protocol_input(str)
    def input_coordinate_file(self, value):
        """The coordinates to minimise."""
        pass

    @protocol_input(System)
    def system(self, value):
        """The system object which defines the forces present in the system."""
        pass

    @protocol_output(str)
    def output_coordinate_file(self):
        """The file path to the minimised coordinates."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        # inputs
        self._input_coordinate_file = None
        self._system = None

        # outputs
        self._output_coordinate_file = None

    def execute(self, directory, available_resources):

        logging.info('Minimising energy: ' + self.id)

        integrator = openmm.VerletIntegrator(0.002 * unit.picoseconds)

        input_pdb_file = app.PDBFile(self._input_coordinate_file)

        simulation = None

        if available_resources.number_of_gpus > 0:

            gpu_platform = Platform.getPlatformByName('CUDA')
            properties = {'DeviceIndex': ','.join(range(available_resources.number_of_gpus))}

            simulation = app.Simulation(input_pdb_file.topology, self._system, integrator, gpu_platform, properties)

            logging.info('Setting up a simulation with {} gpu\'s'.format(available_resources.number_of_gpus))

        else:

            cpu_platform = Platform.getPlatformByName('CPU')
            properties = {'Threads': str(available_resources.number_of_threads)}

            simulation = app.Simulation(input_pdb_file.topology, self._system, integrator, cpu_platform, properties)

            logging.info('Setting up a simulation with {} threads'.format(available_resources.number_of_threads))

        simulation.context.setPositions(input_pdb_file.positions)

        simulation.minimizeEnergy()

        positions = simulation.context.getState(getPositions=True).getPositions()

        self._output_coordinate_file = path.join(directory, 'minimised.pdb')

        with open(self._output_coordinate_file, 'w+') as minimised_file:
            app.PDBFile.writeFile(simulation.topology, positions, minimised_file)

        logging.info('Energy minimised: ' + self.id)

        return self._get_output_dictionary()


@register_calculation_protocol()
class RunOpenMMSimulation(BaseProtocol):
    """Performs a molecular dynamics simulation in a given ensemble using
    an OpenMM backend.
    """

    @protocol_input(int, merge_behavior=MergeBehaviour.GreatestValue)
    def steps(self):
        """The number of timesteps to evolve the system by."""
        pass

    @protocol_input(unit.Quantity, merge_behavior=MergeBehaviour.SmallestValue)
    def thermostat_friction(self):
        """The thermostat friction coefficient."""
        pass

    @protocol_input(unit.Quantity, merge_behavior=MergeBehaviour.SmallestValue)
    def timestep(self):
        """The timestep to evolve the system by at each step."""
        pass

    @protocol_input(int, merge_behavior=MergeBehaviour.SmallestValue)
    def output_frequency(self):
        """The frequency with which to write to the output statistics and trajectory files."""
        pass

    @protocol_input(Ensemble)
    def ensemble(self):
        """The thermodynamic ensemble to simulate in."""
        pass

    @protocol_input(ThermodynamicState)
    def thermodynamic_state(self):
        """The thermodynamic conditions to simulate under"""
        pass

    @protocol_input(str)
    def input_coordinate_file(self):
        """The file path to the starting coordinates."""
        pass

    @protocol_input(System)
    def system(self):
        """The system object which defines the forces present in the system."""
        pass

    @protocol_output(str)
    def output_coordinate_file(self):
        """The file path to the coordinates of the final system configuration."""
        pass

    @protocol_output(str)
    def trajectory_file_path(self):
        """The file path to the trajectory sampled during the simulation."""
        pass

    @protocol_output(str)
    def statistics_file_path(self):
        """The file path to the statistics sampled during the simulation."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        self._steps = 1000

        self._thermostat_friction = 1.0 / unit.picoseconds
        self._timestep = 0.001 * unit.picoseconds

        self._output_frequency = 1000

        self._ensemble = Ensemble.NPT

        # keep a track of the simulation object in case we need to restart.
        self._simulation_object = None

        # inputs
        self._input_coordinate_file = None
        self._thermodynamic_state = None
        self._system = None

        # outputs
        self._output_coordinate_file = None

        self._trajectory_file_path = None
        self._statistics_file_path = None

        self._temporary_statistics_path = None

    def execute(self, directory, available_resources):

        temperature = self._thermodynamic_state.temperature
        pressure = self._thermodynamic_state.pressure

        if temperature is None:

            return PropertyEstimatorException(directory=directory,
                                              message='A temperature must be set to perform '
                                                       'a simulation in any ensemble')

        if Ensemble(self._ensemble) == Ensemble.NPT and pressure is None:

            return PropertyEstimatorException(directory=directory,
                                              message='A pressure must be set to perform an NPT simulation')

        logging.info('Performing a simulation in the ' + str(self._ensemble) + ' ensemble: ' + self.id)

        if self._simulation_object is None:
            self._simulation_object = self._setup_new_simulation(directory, temperature, pressure, available_resources)

        try:
            self._simulation_object.step(self._steps)
        except Exception as e:

            return PropertyEstimatorException(directory=directory,
                                              message='Simulation failed: {}'.format(e))

        # Save the newly generated statistics data as a pandas csv file.
        pressure = None if self._ensemble == Ensemble.NVT else self._thermodynamic_state.pressure

        working_statistics = Statistics.from_openmm_csv(self._temporary_statistics_path, pressure)
        working_statistics.save_as_pandas_csv(self._statistics_file_path)

        positions = self._simulation_object.context.getState(getPositions=True).getPositions()

        input_pdb_file = app.PDBFile(self._input_coordinate_file)
        self._output_coordinate_file = path.join(directory, 'output.pdb')

        logging.info('Simulation performed in the ' + str(self._ensemble) + ' ensemble: ' + self.id)

        with open(self._output_coordinate_file, 'w+') as configuration_file:

            app.PDBFile.writeFile(input_pdb_file.topology,
                                  positions, configuration_file)

        return self._get_output_dictionary()

    def _setup_new_simulation(self, directory, temperature, pressure, available_resources):
        """Creates a new OpenMM simulation object.

        Parameters
        ----------
        directory: str
            The directory in which the object will produce output files.
        temperature: unit.Quantiy
            The temperature at which to run the simulation
        pressure: unit.Quantiy
            The pressure at which to run the simulation
        available_resources: PropertyEstimatorBackendResources
            The resources available to run on.
        """

        # For now set some 'best guess' thermostat parameters.
        integrator = openmm.LangevinIntegrator(temperature,
                                               self._thermostat_friction,
                                               self._timestep)

        system = self._system

        if Ensemble(self._ensemble) == Ensemble.NPT:

            barostat = openmm.MonteCarloBarostat(pressure, temperature)

            # inputs are READONLY! Never directly alter an input
            system = copy.deepcopy(system)
            system.addForce(barostat)

        input_pdb_file = app.PDBFile(self._input_coordinate_file)

        simulation = None

        if available_resources.number_of_gpus > 0:

            gpu_platform = Platform.getPlatformByName('CUDA')
            properties = {'DeviceIndex': ','.join(range(available_resources.number_of_gpus))}

            simulation = app.Simulation(input_pdb_file.topology, system, integrator, gpu_platform, properties)

            logging.info('Setting up a simulation with {} gpu\'s'.format(available_resources.number_of_gpus))

        else:

            cpu_platform = Platform.getPlatformByName('CPU')
            properties = {'Threads': str(available_resources.number_of_threads)}

            simulation = app.Simulation(input_pdb_file.topology, system, integrator, cpu_platform, properties)

            logging.info('Setting up a simulation with {} threads'.format(available_resources.number_of_threads))

        # simulation = app.Simulation(input_pdb_file.topology, system, integrator)

        box_vectors = input_pdb_file.topology.getPeriodicBoxVectors()

        if box_vectors is None:
            box_vectors = system.getDefaultPeriodicBoxVectors()

        simulation.context.setPeriodicBoxVectors(*box_vectors)
        simulation.context.setPositions(input_pdb_file.positions)
        simulation.context.setVelocitiesToTemperature(temperature)

        trajectory_path = path.join(directory, 'trajectory.dcd')
        statistics_path = path.join(directory, 'statistics.csv')

        self._temporary_statistics_path = path.join(directory, 'temp_statistics.csv')

        self._trajectory_file_path = trajectory_path
        self._statistics_file_path = statistics_path

        configuration_path = path.join(directory, 'input.pdb')

        with open(configuration_path, 'w+') as configuration_file:

            app.PDBFile.writeFile(input_pdb_file.topology,
                                  input_pdb_file.positions, configuration_file)

        simulation.reporters.append(app.DCDReporter(trajectory_path, self._output_frequency))

        simulation.reporters.append(app.StateDataReporter(self._temporary_statistics_path, self._output_frequency,
                                                          step=True, potentialEnergy=True, kineticEnergy=True,
                                                          totalEnergy=True, temperature=True, volume=True,
                                                          density=True))

        return simulation


@register_calculation_protocol()
class AveragePropertyProtocol(BaseProtocol):
    """An abstract base class for protocols which will calculate the
    average of a property and its uncertainty via bootstrapping.
    """

    @protocol_input(int, merge_behavior=MergeBehaviour.GreatestValue)
    def bootstrap_iterations(self):
        """The number of bootstrap iterations to perform."""
        pass

    @protocol_input(float, merge_behavior=MergeBehaviour.GreatestValue)
    def bootstrap_sample_size(self):
        """The relative sample size to use for bootstrapping."""
        pass

    @protocol_output(unit.Quantity)
    def value(self):
        """The averaged value."""
        pass

    @protocol_output(unit.Quantity)
    def uncertainty(self):
        """The uncertainty in the average, as calculated by bootstrapping."""
        pass

    @protocol_output(int)
    def equilibration_index(self):
        """The index in the data set after which the data is stationary."""
        pass

    @protocol_output(float)
    def statistical_inefficiency(self):
        """The statistical inefficiency in the data set."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        self._bootstrap_iterations = 100
        self._bootstrap_sample_size = 1.0

        self._value = None
        self._uncertainty = None

        self._equilibration_index = None
        self._statistical_inefficiency = None

    def _bootstrap_function(self, sample_data):
        """The function to perform on the data set being sampled by
        bootstrapping.

        Parameters
        ----------
        sample_data: np.ndarray, shape=(num_frames, num_dimensions), dtype=float
            A sample of the full data set.

        Returns
        -------
        float
            The result of evaluating the data.
        """
        return sample_data.mean()

    def _perform_bootstrapping(self, data_set):
        """Performs bootstrapping on a data set to calculate the
        average value, and the standard error in the average,
        bootstrapping.

        Parameters
        ----------
        data_set: np.ndarray, shape=(num_frames, num_dimensions), dtype=float
            The data set to perform bootstrapping on.

        Returns
        -------
        float
            The average of the data.
        float
            The uncertainty in the average.
        """

        if data_set is None:
            raise ValueError('There is no data to bootstrap in protocol {}'.format(self.id))

        # Make a copy of the data so we don't accidentally destroy anything.
        data_to_bootstrap = np.array(data_set)

        data_size = len(data_to_bootstrap)

        # Choose the sample size as a percentage of the full data set.
        sample_size = min(math.floor(data_size * self._bootstrap_sample_size), data_size)

        average_values = np.zeros(self._bootstrap_iterations)

        for bootstrap_iteration in range(self._bootstrap_iterations):

            sample_indices = np.random.choice(data_size, sample_size)
            sample_data = data_to_bootstrap[sample_indices]

            average_values[bootstrap_iteration] = self._bootstrap_function(sample_data)

        average_value = self._bootstrap_function(data_to_bootstrap)
        uncertainty = average_values.std() * len(average_values) ** -0.5

        if isinstance(average_value, np.float32) or isinstance(average_value, np.float64):
            average_value = average_value.item()

        if isinstance(uncertainty, np.float32) or isinstance(uncertainty, np.float64):
            uncertainty = uncertainty.item()

        return average_value, uncertainty

    def execute(self, directory, available_resources):
        return self._get_output_dictionary()


@register_calculation_protocol()
class AverageTrajectoryProperty(AveragePropertyProtocol):
    """An abstract base class for protocols which will calculate the
    average of a property from a simulation trajectory.
    """

    @protocol_input(str)
    def input_coordinate_file(self):
        """The file path to the starting coordinates of a trajectory."""
        pass

    @protocol_input(str)
    def trajectory_path(self):
        """The file path to the trajectory to average over."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        self._input_coordinate_file = None
        self._trajectory_path = None

        self.trajectory = None

    def execute(self, directory, available_resources):

        if self._trajectory_path is None:

            return PropertyEstimatorException(directory=directory,
                                              message='The AverageTrajectoryProperty protocol '
                                                       'requires a previously calculated trajectory')

        self.trajectory = mdtraj.load_dcd(filename=self._trajectory_path, top=self._input_coordinate_file)

        return self._get_output_dictionary()


@register_calculation_protocol()
class ExtractAverageStatistic(AveragePropertyProtocol):
    """Extracts the average value from a statistics file which was generated
    during a simulation.
    """

    @protocol_input(str)
    def statistics_path(self):
        """The file path to the trajectory to average over."""
        pass

    @protocol_input(AvailableQuantities)
    def statistics_type(self):
        """The file path to the trajectory to average over."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        self._statistics_path = None
        self._statistics_type = AvailableQuantities.PotentialEnergy

        self._statistics = None

    def execute(self, directory, available_resources):

        logging.info('Extracting {}: {}'.format(self._statistics_type, self.id))

        if self._statistics_path is None:

            return PropertyEstimatorException(directory=directory,
                                              message='The ExtractAverageStatistic protocol '
                                                       'requires a previously calculated statistics file')

        self._statistics = Statistics.from_pandas_csv(self.statistics_path)

        values = self._statistics.get_statistics(self._statistics_type)

        if values is None or len(values) == 0:

            return PropertyEstimatorException(directory=directory,
                                              message='The {} statistics file contains no '
                                                      'data.'.format(self._statistics_path))

        statistics_unit = values[0].unit
        values /= statistics_unit

        values = np.array(values)

        values, self._equilibration_index, self._statistical_inefficiency = \
            timeseries.decorrelate_time_series(values)

        self._value, self._uncertainty = self._perform_bootstrapping(values)

        self._value = unit.Quantity(self._value, statistics_unit)
        self._uncertainty = unit.Quantity(self._uncertainty, statistics_unit)

        logging.info('Extracted {}: {}'.format(self._statistics_type, self.id))

        return self._get_output_dictionary()


@register_calculation_protocol()
class ExtractUncorrelatedData(BaseProtocol):
    """An abstract base class for protocols which will subsample
    a data set, yielding only equilibrated, uncorrelated data.
    """

    @protocol_input(int)
    def equilibration_index(self):
        """The index in the data set after which the data is stationary."""
        pass

    @protocol_input(float)
    def statistical_inefficiency(self):
        """The statistical inefficiency in the data set."""
        pass

    def __init__(self, protocol_id):
        super().__init__(protocol_id)

        self._equilibration_index = None
        self._statistical_inefficiency = None

    def execute(self, directory, available_resources):
        raise NotImplementedError


@register_calculation_protocol()
class ExtractUncorrelatedTrajectoryData(ExtractUncorrelatedData):
    """A protocol which will subsample frames from a trajectory, yielding only uncorrelated 
    frames as determined from a provided statistical inefficiency and equilibration time.
    """

    @protocol_input(str)
    def input_coordinate_file(self):
        """The file path to the starting coordinates of a trajectory."""
        pass

    @protocol_input(str)
    def input_trajectory_path(self):
        """The file path to the trajectory to subsample."""
        pass

    @protocol_output(str)
    def output_trajectory_path(self):
        """The file path to the subsampled trajectory."""
        pass

    def __init__(self, protocol_id):

        super().__init__(protocol_id)

        self._input_coordinate_file = None
        self._input_trajectory_path = None

        self._output_trajectory_path = None

    def execute(self, directory, available_resources):

        logging.info('Subsampling trajectory: {}'.format(self.id))

        if self._input_trajectory_path is None:

            return PropertyEstimatorException(directory=directory,
                                              message='The ExtractUncorrelatedTrajectoryData protocol '
                                                       'requires a previously calculated trajectory')

        trajectory = mdtraj.load_dcd(filename=self._input_trajectory_path, top=self._input_coordinate_file)

        uncorrelated_indices = timeseries.get_uncorrelated_indices(trajectory.n_frames, self._statistical_inefficiency)
        uncorrelated_trajectory = trajectory[uncorrelated_indices]

        self._output_trajectory_path = path.join(directory, 'uncorrelated_trajectory.dcd')
        uncorrelated_trajectory.save_dcd(self._output_trajectory_path)

        logging.info('Trajectory subsampled: {}'.format(self.id))

        return self._get_output_dictionary()
