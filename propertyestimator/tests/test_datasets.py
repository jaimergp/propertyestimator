"""
Units tests for propertyestimator.datasets
"""

from os import listdir
from os.path import isfile, join

import logging

import pytest

from propertyestimator.utils import get_data_filename

from propertyestimator.properties import PhysicalProperty
from propertyestimator.datasets import ThermoMLDataSet

# .. todo: Add tests for specific ThermoML data sets that give 100% coverage.
#          These may need to be hand written.


@pytest.mark.skip(reason="Uncertainties have been unexpectedly removed from ThermoML "
                         "so these tests will fail until they have been re-added")
def test_from_url():

    data_set = ThermoMLDataSet.from_url('https://trc.nist.gov/journals/jct/2005v37/i04/j.jct.2004.09.022.xml')
    assert data_set is not None

    assert len(data_set.properties) > 0

    data_set = ThermoMLDataSet.from_url('https://trc.nist.gov/journals/jct/2005v37/i04/j.jct.2004.09.022.xmld')
    assert data_set is None


@pytest.mark.skip(reason="Uncertainties have been unexpectedly removed from ThermoML "
                         "so these tests will fail until they have been re-added")
def test_serialization():

    data_set = ThermoMLDataSet.from_doi('10.1016/j.jct.2016.10.001')
    assert data_set is not None

    assert len(data_set.properties) > 0

    for mixture_tag in data_set.properties:

        for physical_property in data_set.properties[mixture_tag]:
            physical_property_json = physical_property.json()
            print(physical_property_json)

            physical_property_recreated = PhysicalProperty.parse_json(physical_property_json)
            print(physical_property_recreated)


@pytest.mark.skip(reason="Uncertainties have been unexpectedly removed from ThermoML "
                         "so these tests will fail until they have been re-added")
def test_from_doi():

    data_set = ThermoMLDataSet.from_doi('10.1016/j.jct.2016.10.001')
    assert data_set is not None

    assert len(data_set.properties) > 0

    for mixture_tag in data_set.properties:

        for physical_property in data_set.properties[mixture_tag]:

            physical_property_json = physical_property.json()
            print(physical_property_json)

            physical_property_recreated = PhysicalProperty.parse_json(physical_property_json)
            print(physical_property_recreated)

    data_set = ThermoMLDataSet.from_doi('10.1016/j.jct.2016.12.009')
    assert data_set is None

    data_set = ThermoMLDataSet.from_doi('10.1016/j.jct.2016.12.009x')
    assert data_set is None


def test_from_files():

    data_set = ThermoMLDataSet.from_file(get_data_filename('properties/j.jct.2004.09.014.xml'),
                                         get_data_filename('properties/j.jct.2004.09.022.xml'),
                                         get_data_filename('properties/j.jct.2007.09.004.xml'))
    assert data_set is not None

    assert len(data_set.properties) > 0

    data_set = ThermoMLDataSet.from_file('properties/j.jct.2004.09.014.xmld')
    assert data_set is None


def parse_all_jct_files():

    logging.basicConfig(filename='data_sets.log', filemode='w', level=logging.INFO)

    data_path = get_data_filename('properties/JCT')

    thermoml_files = []

    for file_path in listdir(data_path):

        full_path = join(data_path, file_path)

        if not isfile(full_path):
            continue

        thermoml_files.append(full_path)

    data_set = ThermoMLDataSet.from_file(*thermoml_files)

    from propertyestimator.properties.density import Density
    from propertyestimator.properties.dielectric import DielectricConstant
    from propertyestimator.properties.enthalpy import EnthalpyOfMixing

    properties_by_type = {Density.__name__: [], DielectricConstant.__name__: [],
                          EnthalpyOfMixing.__name__: []}

    for substance_key in data_set.properties:

        for data_property in data_set.properties[substance_key]:

            if type(data_property).__name__ not in properties_by_type:
                continue

            properties_by_type[type(data_property).__name__].append(data_property.source.reference)

    for type_key in properties_by_type:

        with open('{}.dat'.format(type_key), 'w') as file:

            for doi in properties_by_type[type_key]:
                file.write('{}\n'.format(doi))


if __name__ == "__main__":
    parse_all_jct_files()
