"""
A collection of general utilities.
"""

import copy
import logging
import os
import sys


def find_types_with_decorator(class_type, decorator_type):
    """ A method to collect all attributes marked by a specified
    decorator type (e.g. InputProperty).

    Parameters
    ----------
    class_type: class
        The class to pull attributes from.
    decorator_type: str
        The type of decorator to search for.

    Returns
    ----------
    The names of the attributes decorated with the specified decorator.
    """
    inputs = []

    def get_bases(current_base_type):

        bases = [current_base_type]

        for base_type in current_base_type.__bases__:
            bases.extend(get_bases(base_type))

        return bases

    all_bases = get_bases(class_type)

    for base in all_bases:

        inputs.extend([attribute_name for attribute_name in base.__dict__ if
                       type(base.__dict__[attribute_name]).__name__ == decorator_type])

    return inputs


def get_data_filename(relative_path):
    """Get the full path to one of the reference files in data.

    In the source distribution, these files are in ``propertyestimator/data/``,
    but on installation, they're moved to somewhere in the user's python
    site-packages directory.

    Parameters
    ----------
    relative_path : str
        The relative path of the file to load.
    """

    from pkg_resources import resource_filename
    fn = resource_filename('propertyestimator', os.path.join('data', relative_path))

    if not os.path.exists(fn):
        raise ValueError("Sorry! %s does not exist. If you just added it, you'll have to re-install" % fn)

    return fn


_cached_molecules = {}


def create_molecule_from_smiles(smiles):
    """
    Create an ``OEMol`` molecule from a smiles pattern.

    .. todo:: Replace with the toolkit function when finished.

    Parameters
    ----------
    smiles : str
        Smiles pattern

    Returns
    -------
    molecule : OEMol
        OEMol with 3D coordinates, but no charges
     """

    from openeye import oechem, oeomega

    # Check cache
    if smiles in _cached_molecules:
        return copy.deepcopy(_cached_molecules[smiles])

    # Create molecule from smiles.
    molecule = oechem.OEMol()
    parse_smiles_options = oechem.OEParseSmilesOptions(quiet=True)

    if not oechem.OEParseSmiles(molecule, smiles, parse_smiles_options):

        logging.warning('Could not parse SMILES: ' + smiles)
        return False

    # Normalize molecule
    oechem.OEAssignAromaticFlags(molecule, oechem.OEAroModelOpenEye)
    oechem.OEAddExplicitHydrogens(molecule)
    oechem.OETriposAtomNames(molecule)

    # Create configuration
    omega = oeomega.OEOmega()

    omega.SetMaxConfs(1)
    omega.SetIncludeInput(False)
    omega.SetCanonOrder(False)
    omega.SetSampleHydrogens(True)
    omega.SetStrictStereo(True)
    omega.SetStrictAtomTypes(False)

    status = omega(molecule)

    if not status:

        logging.warning('Could not generate a conformer for ' + smiles)
        return False

    _cached_molecules[smiles] = molecule

    return molecule


def setup_timestamp_logging():
    """Set up timestamp-based logging."""
    formatter = logging.Formatter(fmt='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
                                  datefmt='%H:%M:%S')

    screen_handler = logging.StreamHandler(stream=sys.stdout)
    screen_handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(screen_handler)


def get_unitless_array(array):
    """Separates a simtk.unit.Quantitiy np.ndarray array into a
    tuple of the unitless array and its original unit.

    Parameters
    ----------
    array: np.ndarray of unit.Quantity
        The array to separate.

    Returns
    -------
    np.ndarray of float
        The unitless array.
    simtk.unit.Quantity
        The corresponding unit of the array.
    """

    from simtk import unit
    assert isinstance(array, unit.Quantity)

    array_in_default_unit_system = array.in_unit_system(unit.md_unit_system)

    array_unit = array_in_default_unit_system.unit
    unitless_array = array.value_in_unit(array_unit)

    return unitless_array, array_unit
