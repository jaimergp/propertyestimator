"""
A collection of classes which aid in serializing data types.
"""

import importlib
import inspect
import json
import numpy as np
from abc import ABC, abstractmethod
from enum import Enum
from io import BytesIO

from simtk import unit

from propertyestimator.utils.quantities import EstimatedQuantity


def _type_string_to_object(type_string):
    last_period_index = type_string.rfind('.')

    if last_period_index < 0 or last_period_index == len(type_string) - 1:
        raise ValueError('The type string is invalid - it should be of the form '
                         'module_path.class_name: {}'.format(type_string))

    module_name = type_string[0:last_period_index]
    module = importlib.import_module(module_name)

    class_name = type_string[last_period_index + 1:]

    if class_name == 'NoneType':
        return None

    class_name_split = class_name.split('->')
    class_object = module

    while len(class_name_split) > 0:
        class_name_current = class_name_split.pop(0)
        class_object = getattr(class_object, class_name_current)

    return class_object


def serialize_quantity(quantity):
    """
    Serialized a simtk.unit.Quantity into a dict of {'unitless_value': X, 'unit': Y}

    .. todo:: Currently duplicates Jeff Wagners implementation.

    Parameters
    ----------
    quantity : A simtk.unit.Quantity-wrapped value or iterator over values
        The object to serialize

    Returns
    -------
    serialzied : dict
        The serialized object
    """

    if not isinstance(quantity, unit.Quantity):
        raise ValueError('{} is not a Quantity'.format(type(quantity)))

    serialized = dict()

    # If it's None, just return None in all fields
    if quantity is None:
        serialized['unitless_value'] = None
        serialized['unit'] = None
        return serialized

    # If it's not None, make sure it's a simtk.unit.Quantity
    assert (hasattr(quantity, 'unit'))

    quantity_unit = list()
    for base_unit in quantity.unit.iter_all_base_units():
        quantity_unit.append((base_unit[0].name, base_unit[1]))

    conversion_factor = quantity.unit.get_conversion_factor_to_base_units()

    unitless_value = (quantity / quantity.unit) * conversion_factor
    serialized['unitless_value'] = unitless_value
    serialized['unit'] = quantity_unit
    return serialized


def deserialize_quantity(serialized):
    """
    Deserializes a simtk.unit.Quantity.

    .. todo:: Currently duplicates Jeff Wagners implementation.

    Parameters
    ----------
    serialized : dict
        Serialized representation of a simtk.unit.Quantity. Must have keys ["unitless_value", "unit"]

    Returns
    -------
    simtk.unit.Quantity
    """

    if '@type' in serialized:
        serialized.pop('@type')

    if (serialized['unitless_value'] is None) and (serialized['unit'] is None):
        return None
    quantity_unit = None
    for unit_name, power in serialized['unit']:
        unit_name = unit_name.replace(' ', '_')  # Convert eg. 'elementary charge' to 'elementary_charge'
        if quantity_unit is None:
            quantity_unit = (getattr(unit, unit_name) ** power)
        else:
            quantity_unit *= (getattr(unit, unit_name) ** power)
    quantity = unit.Quantity(serialized['unitless_value'], quantity_unit)
    return quantity


def deserialize_estimated_quantity(quantity_dictionary):
    """
    Deserializes an EstimatedQuantity.

    Parameters
    ----------
    quantity_dictionary : dict of str and Any
        Serialized representation of an EstimatedQuantity, generated by the
        `EstimatedQuantity.__getstate__` method

    Returns
    -------
    EstimatedQuantity
    """

    if '@type' in quantity_dictionary:
        quantity_dictionary.pop('@type')

    return_object = EstimatedQuantity(unit.Quantity(), unit.Quantity(), 'empty_source')
    return_object.__setstate__(quantity_dictionary)

    return return_object


def serialize_force_field(force_field):
    """A method for turning an `openforcefield.typing.engines.smirnoff.ForceField`
    object into a dictionary of int and str.

    Notes
    -----
    The value in the dictionary is
    temporarily for now just the xml representation of the force field.

    Parameters
    ----------
    force_field: openforcefield.typing.engines.smirnoff.ForceField
        The force field to serialize.
    Returns
    -------
    Dict[int, str]
        The serialised force field, where the keys are int indices, and
        the values are the xml of the serialized force field trees.
    """

    from openforcefield.typing.engines.smirnoff import ForceField

    if not isinstance(force_field, ForceField):
        raise ValueError('{} is not a ForceField'.format(type(force_field)))

    file_buffers = tuple([BytesIO() for _ in force_field._XMLTrees])

    force_field.writeFile(file_buffers)

    return_dictionary = {}

    for index, file_buffer in enumerate(file_buffers):

        string_value = file_buffer.getvalue().decode()
        return_dictionary[index] = string_value

        file_buffer.close()

    return return_dictionary


def deserialize_force_field(force_field_dictionary):
    """A method for deserializing a force field which has been
    serialized as a dictionary by the `serialize_force_field` method.

    Notes
    -----
    The value in the dictionary is temporarily for now just the xml
    representation of the force field.

    Parameters
    ----------
    force_field_dictionary: Dict[int, str]
        The serialised force field, where each key of the dictionary is an int index,
        each value is an xml representation of the force field.

    Returns
    -------
    openforcefield.typing.engines.smirnoff.ForceField
        The deserialized force field.
    """

    if '@type' in force_field_dictionary:
        force_field_dictionary.pop('@type')

    file_buffers = []

    for index in force_field_dictionary:

        bytes_string = force_field_dictionary[index]

        if isinstance(bytes_string, str):
            bytes_string = bytes_string.encode('utf-8')

        file_buffers.append(BytesIO(bytes_string))

    from openforcefield.typing.engines.smirnoff import ForceField

    force_field = ForceField(*file_buffers)
    return force_field


def serialize_enum(enum):

    if not isinstance(enum, Enum):
        raise ValueError('{} is not an Enum'.format(type(enum)))

    return {
        'value': enum.value
    }


def deserialize_enum(enum_dictionary):

    if '@type' not in enum_dictionary:

        raise ValueError('The serialized enum dictionary must include'
                         'which type the enum is.')

    if 'value' not in enum_dictionary:

        raise ValueError('The serialized enum dictionary must include'
                         'the enum value.')

    enum_type_string = enum_dictionary['@type']
    enum_value = enum_dictionary['value']

    enum_class = _type_string_to_object(enum_type_string)

    if not issubclass(enum_class, Enum):
        raise ValueError('<{}> is not an Enum'.format(enum_class))

    return enum_class(enum_value)


class TypedJSONEncoder(json.JSONEncoder):

    _natively_supported_types = [
        dict, list, tuple, str, int, float, bool
    ]

    _custom_supported_types = {
        Enum: serialize_enum,
        unit.Quantity: serialize_quantity,
        'ForceField': serialize_force_field,
        np.float16: lambda x: { 'value': float(x) },
        np.float32: lambda x: { 'value': float(x) },
        np.float64: lambda x: { 'value': float(x) },
        np.int32: lambda x: {'value': int(x)},
        np.int64: lambda x: {'value': int(x)},
        np.ndarray: lambda x: {'value': x.tolist()},
    }

    def default(self, value_to_serialize):

        if value_to_serialize is None:
            return None

        type_to_serialize = type(value_to_serialize)

        if type_to_serialize in TypedJSONEncoder._natively_supported_types:
            # If the value is a native type, then let the default serializer
            # handle it.
            return super(TypedJSONEncoder, self).default(value_to_serialize)

        # Otherwise, we need to add a @type attribute to it.
        qualified_name = type_to_serialize.__qualname__
        qualified_name = qualified_name.replace('.', '->')

        type_tag = '{}.{}'.format(type_to_serialize.__module__, qualified_name)
        serializable_dictionary = {}

        custom_encoder = None

        for encoder_type in TypedJSONEncoder._custom_supported_types:

            if isinstance(encoder_type, str):

                if encoder_type != qualified_name:
                    continue

            elif not issubclass(type_to_serialize, encoder_type):
                continue

            custom_encoder = TypedJSONEncoder._custom_supported_types[encoder_type]
            break

        if custom_encoder is not None:

            try:
                serializable_dictionary = custom_encoder(value_to_serialize)

            except Exception as e:

                raise ValueError('{} ({}) could not be serialized '
                                 'using a specialized custom encoder: {}'.format(value_to_serialize,
                                                                                 type_to_serialize, e))

        elif hasattr(value_to_serialize, '__getstate__'):

            try:
                serializable_dictionary = value_to_serialize.__getstate__()

            except Exception as e:

                raise ValueError('{} ({}) could not be serialized '
                                 'using its __getstate__ method: {}'.format(value_to_serialize,
                                                                            type_to_serialize, e))

        else:

            raise ValueError('Objects of type {} are not serializable, please either'
                             'add a __getstate__ method, or add the object to the list'
                             'of custom supported types.'.format(type_to_serialize))

        serializable_dictionary['@type'] = type_tag
        return serializable_dictionary


class TypedJSONDecoder(json.JSONDecoder):

    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    _custom_supported_types = {
        Enum: deserialize_enum,
        unit.Quantity: deserialize_quantity,
        EstimatedQuantity: deserialize_estimated_quantity,
        'ForceField': deserialize_force_field,
        np.float16: lambda x: np.float16(x['value']),
        np.float32: lambda x: np.float32(x['value']),
        np.float64: lambda x: np.float64(x['value']),
        np.int32: lambda x: np.int32(x['value']),
        np.int64: lambda x: np.int64(x['value']),
        np.ndarray: lambda x: np.array(x)
    }

    @staticmethod
    def object_hook(object_dictionary):

        if '@type' not in object_dictionary:
            return object_dictionary

        type_string = object_dictionary['@type']
        class_type = _type_string_to_object(type_string)

        deserialized_object = None

        custom_decoder = None

        for decoder_type in TypedJSONDecoder._custom_supported_types:

            if isinstance(decoder_type, str):

                if decoder_type != class_type.__qualname__:
                    continue

            elif not issubclass(class_type, decoder_type):
                continue

            custom_decoder = TypedJSONDecoder._custom_supported_types[decoder_type]
            break

        if custom_decoder is not None:

            try:
                deserialized_object = custom_decoder(object_dictionary)

            except Exception as e:

                raise ValueError('{} ({}) could not be deserialized '
                                 'using a specialized custom decoder: {}'.format(object_dictionary,
                                                                                 type(class_type), e))

        elif hasattr(class_type, '__setstate__'):

            try:

                class_init_signature = inspect.signature(class_type)

                for parameter in class_init_signature.parameters.values():

                    if (parameter.default != inspect.Parameter.empty or
                        parameter.kind == inspect.Parameter.VAR_KEYWORD or
                        parameter.kind == inspect.Parameter.VAR_POSITIONAL):

                        continue

                    raise ValueError('Cannot deserialize objects which have '
                                     'non-optional arguments {} in the constructor: {}.'.format(parameter.name,
                                                                                                class_type))

                deserialized_object = class_type()
                deserialized_object.__setstate__(object_dictionary)

            except Exception as e:

                raise ValueError('{} ({}) could not be deserialized '
                                 'using its __setstate__ method: {}'.format(object_dictionary,
                                                                            type(class_type), e))

        else:

            raise ValueError('Objects of type {} are not deserializable, please either'
                             'add a __setstate__ method, or add the object to the list'
                             'of custom supported types.'.format(type(class_type)))

        return deserialized_object


class TypedBaseModel(ABC):
    """An abstract base class which represents any object which
    can be serialized to JSON.

    JSON produced using this class will include extra @type tags
    for any non-primitive typed values (e.g not a str, int...),
    which ensure that the correct class structure is correctly
    reproduced on deserialization.

    EXAMPLE

    It is a requirement that any classes inheriting from this one
    must implement a valid `__getstate__` and `__setstate__` method,
    as these are what determines the structure of the serialized
    output.
    """

    def json(self):
        """Creates a JSON representation of this class.

        Returns
        -------
        str
            The JSON representation of this class.
        """
        json_string = json.dumps(self, cls=TypedJSONEncoder)
        return json_string

    @classmethod
    def parse_json(cls, string_contents, encoding='utf8'):
        """Parses a typed json string into the corresponding class
        structure.

        Parameters
        ----------
        string_contents: str or bytes
            The typed json string.
        encoding: str
            The encoding of the `string_contents`.

        Returns
        -------
        Any
            The parsed class.
        """
        return_object = json.loads(string_contents, encoding=encoding, cls=TypedJSONDecoder)
        return return_object

    @abstractmethod
    def __getstate__(self):
        """Returns a dictionary representation of this object.

        Returns
        -------
        dict of str, Any
            The dictionary representation of this object.
        """
        pass

    @abstractmethod
    def __setstate__(self, state):
        """Sets the fields of this object from its dictionary representation.

        Parameters
        ----------
        state: dict of str, Any
            The dictionary representation of the object.
        """
        pass
