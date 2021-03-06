Property Estimator
==================================

This module provides an api for multi-fidelity property calculations.

Estimating properties
---------------------

.. warning:: This text is now out of date, but will be updated in future to reflect the
             latest version of the framework.

The :obj:`PropertyEstimatorClient` class creates objects that handle property estimation of all of the properties in a dataset,
given a set or sets of parameters. The implementation will isolate the user from whatever backend (local machine,
HPC cluster, `XSEDE resources <http://xsede.org>`_, `Amazon EC2 <https://aws.amazon.com/ec2>`_) is being used to compute
the properties, as well as whether new simulations are being launched and analyzed or existing simulation data is being
reweighted.

Different backends will take different optional arguments, but here is an example that will launch and use 10 worker
processes on a cluster:

.. code-block:: python

    estimator = PropertyEstimatorClient(nworkers=10) # NOTE: multiple backends will be supported in the future
    computed_properties = estimator.computeProperties(dataset, force_fields)

Here, ``dataset`` is a ``PhysicalPropertyDataset`` or subclass, and ``force_fields`` is a list containing
``ForceField`` objects used to parameterize the physical systems in the dataset.

This can be a single parameter set or multiple (usually closely related) parameter sets.

``PropertyEstimatorClient.computeProperties(...)`` returns a list of ``ComputedPhysicalProperty`` objects that provide access
to several pieces of information:

* ``property.value`` - the computed property value, with appropriate units
* ``property.uncertainty`` - the statistical uncertainty in the computed property
* ``property.parameters`` - a reference to the parameter set used to compute this property
* ``property.property`` - a reference to the corresponding ``MeasuredPhysicalProperty`` this property was computed for

.. todo::

    * How should we instruct ``computeProperties()`` to provide gradients (or components of gradients)?

This API can be extended in the future to provide access to the simulation data used to estimate the property, such as

.. code-block:: python

    # Attach to my compute and storage resources
    estimator = PropertyEstimatorClient(...)
    # Estimate some properties

    computed_properties = estimator.computeProperties(dataset, parameters)
    # Get statistics about simulation data that went into each property

    for property in computed_properties:

       # Get statistics about simulation data that was reweighted to estimate this property
       for simulation in property.simulations:

          print('The simulation was %.3f ns long' % (simulation.length / unit.nanoseconds))
          print('The simulation was run at %.1f K and %.1f atm' % (simulation.thermodynamic_state.temperature /
            unit.kelvin, simulation.thermodynamic_state.pressure / unit.atmospheres))

          # Get the ParameterSet that was used for this simulation
          parameters = simulation.parameters

          # what else do you want...?

In future, we will want to use a parallel key/value database like `cassandra <http://cassandra.apache.org>`_ to store
simulations, along with a distributed task management system like `celery <http://www.celeryproject.org>`_ with
`redis <https://www.google.com/search?client=safari&rls=en&q=redis&ie=UTF-8&oe=UTF-8>`_.

API Usage Examples
------------------

.. warning:: This text is now out of date, but will be updated in future to reflect the
             latest version of the framework.

In this example, datasets are retrieved from the ThermoML and filtered to retain certain properties.

The corresponding properties for a given parameter set filename are then computed for a SMIRFF parameter set and
printed.

.. code-block:: python

    # Define the input datasets from ThermoML
    thermoml_keys = ['10.1016/j.jct.2005.03.012', ...]
    dataset = ThermoMLDataset(thermoml_keys)

    # Filter the dataset to include only molar heat capacities measured between 280-350 K
    dataset.filter(ePropName='Excess molar enthalpy (molar enthalpy of mixing), kJ/mol') # filter to retain only
                                                                                         # this property name

    dataset.filter(VariableType='eTemperature', min=280*unit.kelvin, max=350*kelvin) # retain only measurements with
                                                                                     # `eTemperature` in specified range

    # Load an initial parameter set
    force_field = [ SMIRFFParameterSet('smarty-initial.xml') ]

    # Compute physical properties for these measurements
    estimator = PropertyEstimatorClient(nworkers=10) # NOTE: multiple backends will be supported in the future
    computed_properties = estimator.computeProperties(dataset, force_field)

    # Write out statistics about errors in computed properties
    for (computed, measured) in (computed_properties, dataset):

        property_unit = measured.value.unit

        print('%24s : experiment %8.3f (%.3f) | calculated %8.3f (%.3f) %s' % (measured.value / property_unit,
            measured.uncertainty / property_unit, computed.value / property_unit, computed.uncertainty / property_unit,
            str(property_unit))
