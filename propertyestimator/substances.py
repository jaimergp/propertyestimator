"""
An API for defining and creating substances.
"""

import abc
import math
from enum import Enum

import numpy as np

from propertyestimator.utils.serialization import TypedBaseModel


class Substance(TypedBaseModel):
    """Defines the components, their amounts, and their roles in a system.

    Examples
    --------
    A neat liquid has only one component:

    >>> liquid = Substance()
    >>> liquid.add_component(Substance.Component(smiles='O'), Substance.MoleFraction(1.0))

    A binary mixture has two components, where the mole fractions must be
    explicitly stated:

    >>> binary_mixture = Substance()
    >>> binary_mixture.add_component(Substance.Component(smiles='O'), Substance.MoleFraction(0.2))
    >>> binary_mixture.add_component(Substance.Component(smiles='CO'), Substance.MoleFraction(0.8))

    The infinite dilution of one solute within a solvent or mixture may also specified
    as a `Substance` by setting the mole fraction of the solute equal to 0.0.

    In this example we explicitly flag the benzene component as being the solute, and the
    water component the solvent, to aid in setting up and performing solvation free energy
    calculations:

    >>> benzene = Substance.Component(smiles='C1=CC=CC=C1', role=Substance.ComponentRole.Solute)
    >>> water = Substance.Component(smiles='O', role=Substance.ComponentRole.Solvent)

    >>> infinite_dilution = Substance()
    >>> infinite_dilution.add_component(component=benzene, amount=Substance.ExactAmount(1)) # Infinite dilution.
    >>> infinite_dilution.add_component(component=water, amount=Substance.MoleFraction(1.0))
    """

    class ComponentRole(Enum):
        """An enum which describes the role of a component in the system,
        such as whether the component is a solvent, a solute, a receptor etc.

        These roles are mainly only used by specific protocols to identify
        the correct species in a system, such as when doing docking or performing
        solvation free energy calculations.
        """
        Solvent = 'Solvent'
        Solute = 'Solute'

        Ligand = 'Ligand'
        Receptor = 'Receptor'

        Undefined = 'Undefined'

    class Component(TypedBaseModel):
        """Defines a single component in a system, as well as properties
        such as it's relative proportion in the system.
        """

        @property
        def identifier(self):
            """str: A unique identifier for this component, which is either a
            smiles descriptor or the supplied label."""
            return self._smiles or self._label

        @property
        def label(self):
            """str: A string label which describes this compound, for example, CB8."""
            return self._label

        @property
        def smiles(self):
            """str: The smiles pattern which describes this component, which may be None
            for complex (e.g protein) molecules."""
            return self._smiles

        @property
        def role(self):
            """ComponentRole: The role of this component in the system, such as a
            ligand or a receptor."""
            return self._role

        def __init__(self, smiles=None, label=None, role=None):
            """Constructs a new Component object with either a label or
            a smiles string, but not both.

            Notes
            -----
            The `label` and `smiles` arguments are mutually exclusive, and only
            one can be passed while the other should be `None`.

            Parameters
            ----------
            smiles: str
                A SMILES descriptor of the component
            label: str
                A string label which describes this compound, for example, CB8.
            role: ComponentRole, optional
                The role of this component in the system. If no role is specified,
                a default role of solvent is applied.
            """

            if label == smiles:
                label = None

            assert ((label is None and smiles is not None) or
                    (label is not None and smiles is None) or
                    (label is None and smiles is None))

            label = label if label is not None else smiles

            self._label = label
            self._smiles = smiles

            self._role = role or Substance.ComponentRole.Solvent

        def __getstate__(self):
            return {
                'label': self.label,
                'smiles': self.smiles,

                'role': self.role
            }

        def __setstate__(self, state):
            self._label = state['label']
            self._smiles = state['smiles']

            self._role = state['role']

        def __str__(self):
            return self.identifier

        def __hash__(self):
            return hash((self.identifier, self._role))

        def __eq__(self, other):
            return hash(self) == hash(other)

        def __ne__(self, other):
            return not (self == other)

    class Amount(abc.ABC):
        """An abstract representation of the amount of a given component
        in a substance.
        """

        @property
        def value(self):
            """The value of this amount."""
            return self._value

        @property
        def identifier(self):
            """A string identifier for this amount."""
            raise NotImplementedError()

        def __init__(self, value=None):
            """Constructs a new Amount object."""
            self._value = value

        @abc.abstractmethod
        def to_number_of_molecules(self, total_substance_molecules, tolerance=None):
            """Converts this amount to an exact number of molecules

            Parameters
            ----------
            total_substance_molecules: int
                The total number of molecules in the whole substance. This amount
                will contribute to a portion of this total number.
            tolerance: float
                The tolerance with which this amount should be in. As an example,
                when converting a mole fraction into a number of molecules, the
                total number of molecules may not be sufficently large enough to
                reproduce this amount.

            Returns
            -------
            int
                The number of molecules which this amount represents,
                given the `total_substance_molecules`.
            """
            raise NotImplementedError()

        def __getstate__(self):
            return {'value': self._value}

        def __setstate__(self, state):
            self._value = state['value']

        def __str__(self):
            return self.identifier

        def __eq__(self, other):
            return np.isclose(self._value, other.value)

    class MoleFraction(Amount):
        """Represents the amount of a component in a substance as a
        mole fraction."""

        @property
        def value(self):
            """float: The value of this amount."""
            return super(Substance.MoleFraction, self).value

        @property
        def identifier(self):
            return f'{{{self._value:.6f}}}'

        def __init__(self, value=1.0):
            """Constructs a new MoleFraction object.

            Parameters
            ----------
            value: float
                A mole fraction in the range (0.0, 1.0]
            """

            if value <= 0.0 or value > 1.0:

                raise ValueError('A mole fraction must be greater than zero, and less than or'
                                 'equal to one.')

            if math.floor(value * 1e6) < 1:

                raise ValueError('Mole fractions are only precise to the sixth '
                                 'decimal place.')

            super().__init__(value)

        def to_number_of_molecules(self, total_substance_molecules, tolerance=None):

            # Determine how many molecules of each type will be present in the system.
            number_of_molecules = int(round(self._value * total_substance_molecules))

            if number_of_molecules == 0:
                raise ValueError('The total number of substance molecules was not large enough, '
                                 'such that this non-zero amount translates into zero molecules '
                                 'of this component in the substance.')

            if tolerance is not None:

                mole_fraction = number_of_molecules / total_substance_molecules

                if abs(mole_fraction - self._value) > tolerance:
                    raise ValueError(f'The mole fraction ({mole_fraction}) given a total number of molecules '
                                     f'({total_substance_molecules}) is outside of the tolerance {tolerance} '
                                     f'of the target mole fraction {self._value}')

            return number_of_molecules

    class ExactAmount(Amount):
        """Represents the amount of a component in a substance as an
        exact number of molecules.

        The expectation is that this amount should be used for components which
        are infinitely dilute (such as ligands in binding calculations), and hence
        do not contribute to the total mole fraction of a substance"""

        @property
        def value(self):
            """int: The value of this amount."""
            return super(Substance.ExactAmount, self).value

        @property
        def identifier(self):
            return f'({int(round(self._value)):d})'

        def __init__(self, value):
            """Constructs a new ExactAmount object.

            Parameters
            ----------
            value: int
                An exact number of molecules.
            """

            if not np.isclose(int(round(value)), value):
                raise ValueError('The value must be an integer.')

            super().__init__(value)

        def to_number_of_molecules(self, total_substance_molecules, tolerance=None):
            return self._value

    @property
    def identifier(self):

        component_identifiers = [component.identifier for component in self._components]
        component_identifiers.sort()

        sorted_component_identifiers = [component.identifier for component in self._components]
        sorted_component_identifiers.sort()

        identifier_split = []

        for component_identifier in sorted_component_identifiers:

            component_amount = self._amounts[component_identifier]

            identifier = f'{component_identifier}{component_amount.identifier}'
            identifier_split.append(identifier)

        return '|'.join(identifier_split)

    @property
    def components(self):
        return self._components

    @property
    def number_of_components(self):
        return len(self._components)

    def __init__(self):
        """Constructs a new Substance object."""

        self._amounts = {}
        self._components = []

    def add_component(self, component, amount):
        """Add a component to the Substance. If the component is already present in
        the substance, then the mole fraction will be added to the current mole
        fraction of that component.

        Parameters
        ----------
        component : Substance.Component
            The component to add to the system.
        amount : Substance.Amount
            The amount of this component in the substance.
        """

        assert isinstance(component, Substance.Component)
        assert isinstance(amount, Substance.Amount)

        if isinstance(amount, Substance.MoleFraction):

            total_mole_fraction = amount.value + sum([amount.value for amount in self._amounts if
                                                      isinstance(amount, Substance.MoleFraction)])

            if total_mole_fraction > 1.0:
                raise ValueError(f'The total mole fraction of this substance {total_mole_fraction} exceeds 1.0')

        if component.identifier not in self._amounts:

            self._amounts[component.identifier] = amount
            self._components.append(component)

            return

        existing_amount = self._amounts[component.identifier]

        if not type(existing_amount) is type(amount):

            raise ValueError(f'This component already exists in the substance, but in a '
                             f'different amount type ({type(existing_amount)}) than that '
                             f'specified ({type(amount)})')

        new_amount = type(amount)(existing_amount.value + amount.value)
        self._amounts[component.identifier] = new_amount

    def get_amount(self, component):
        """Returns the amount of the component in this substance.

        Parameters
        ----------
        component: str or Substance.Component
            The component (or it's identifier) to retrieve the mole fraction of.

        Returns
        -------
        Substance.Amount
            The amount of the component in this substance.
        """
        assert isinstance(component, str) or isinstance(component, Substance.Component)
        identifier = component if isinstance(component, str) else component.identifier

        return self._amounts[identifier]

    def get_molecules_per_component(self, maximum_molecules):
        """Returns the number of molecules for each component in this substance,
        given a maximum total number of molecules.

        Parameters
        ----------
        maximum_molecules: int
            The maximum number of molecules.

        Returns
        -------
        dict of str and int
            A dictionary of molecule counts per component, where each key is
            a component identifier.
        """

        number_of_molecules = {}
        remaining_molecule_slots = maximum_molecules

        for index, component in enumerate(self._components):

            amount = self._amounts[component.identifier]

            if not isinstance(amount, Substance.ExactAmount):
                continue

            remaining_molecule_slots -= amount.value

        for index, component in enumerate(self._components):

            amount = self._amounts[component.identifier]
            number_of_molecules[component.identifier] = amount.to_number_of_molecules(remaining_molecule_slots)

        return number_of_molecules

    def __getstate__(self):
        return {
            'components': self._components,
            'amounts': self._amounts
        }

    def __setstate__(self, state):
        self._components = state['components']
        self._amounts = state['amounts']

    def __str__(self):
        return self.identifier

    def __hash__(self):

        sorted_component_identifiers = [component.identifier for component in self._components]
        sorted_component_identifiers.sort()

        component_by_id = {component.identifier: component for component in self._components}

        string_hash_split = []

        for identifier in sorted_component_identifiers:

            component_role = component_by_id[identifier].role
            component_amount = self._amounts[identifier].identifier

            string_hash_split.append(f'{identifier}_{component_role}_{component_amount}')

        string_hash = '|'.join(string_hash_split)

        return hash(string_hash)

    def __eq__(self, other):

        return hash(self) == hash(other)

    def __ne__(self, other):
        return not (self == other)
