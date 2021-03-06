"""
Defines the base API for defining new property estimator estimation layers.
"""
import json
import logging
import traceback
from os import path

from propertyestimator.utils.exceptions import PropertyEstimatorException
from propertyestimator.utils.serialization import TypedJSONDecoder, TypedJSONEncoder

available_layers = {}


def register_calculation_layer():
    """A decorator which registers a class as being a calculation layer
    which may be used in property calculations.
    """

    def decorator(cls):

        if cls.__name__ in available_layers:
            raise ValueError('The {} layer is already registered.'.format(cls.__name__))

        available_layers[cls.__name__] = cls
        return cls

    return decorator


def return_args(*args, **kwargs):
    return args


class CalculationLayerResult:
    """The output returned from attempting to calculate a property on
     a PropertyCalculationLayer."""

    def __init__(self):
        """Constructs a new CalculationLayerResult object.
        """
        self.property_id = None

        self.calculated_property = None
        self.exception = None

        self.data_directories_to_store = []

    def __getstate__(self):

        return {
            'property_id': self.property_id,
            'calculated_property': self.calculated_property,
            'exception': self.exception,
            'data_directories_to_store': self.data_directories_to_store
        }

    def __setstate__(self, state):

        self.property_id = state['property_id']

        self.calculated_property = state['calculated_property']
        self.exception = state['exception']

        self.data_directories_to_store = state['data_directories_to_store']


class PropertyCalculationLayer:
    """An abstract representation of a calculation layer in the property calculation stack.

    Notes
    -----
    Calculation layers must inherit from this class, and must override the
    `schedule_calculation` method.
    """

    @staticmethod
    def _await_results(calculation_backend, storage_backend, layer_directory, server_request,
                       callback, submitted_futures, synchronous=False):
        """A helper method to handle passing the results of this layer back to
        the main thread.

        Parameters
        ----------
        calculation_backend: PropertyEstimatorBackend
            The backend to the submit the calculations to.
        storage_backend: PropertyEstimatorStorage
            The backend used to store / retrieve data from previous calculations.
        layer_directory: str
            The local directory in which to store all local, temporary calculation data from this layer.
        server_request: PropertyEstimatorServer.ServerEstimationRequest
            The request object which spawned the awaited results.
        callback: function
            The function to call when the backend returns the results (or an error).
        submitted_futures: list(dask.distributed.Future)
            A list of the futures returned by the backed when submitting the calculation.
        synchronous: bool
            If true, this function will block until the calculation has completed.
        """

        callback_future = calculation_backend.submit_task(return_args,
                                                          *submitted_futures,
                                                          key=f'return_{server_request.id}')

        def callback_wrapper(results_future):
            PropertyCalculationLayer._process_results(results_future, server_request, storage_backend, callback)

        if synchronous:
            callback_wrapper(callback_future)
        else:
            callback_future.add_done_callback(callback_wrapper)

    @staticmethod
    def _process_results(results_future, server_request, storage_backend, callback):
        """Processes the results of a calculation layer, updates the server request,
        then passes it back to the callback ready for propagation to the next layer
        in the stack.

        Parameters
        ----------
        results_future: distributed.Future
            The future object which will hold the results.
        server_request: PropertyEstimatorServer.ServerEstimationRequest
            The request object which spawned the awaited results.
        storage_backend: PropertyEstimatorStorage
            The backend used to store / retrieve data from previous calculations.
        callback: function
            The function to call when the backend returns the results (or an error).
        """

        # Wrap everything in a try catch to make sure the whole calculation backend /
        # server doesn't go down when an unexpected exception occurs.
        try:

            results = list(results_future.result())
            results_future.release()

            for returned_output in results:

                if returned_output is None:
                    # Indicates the layer could not calculate this
                    # particular property.
                    continue

                if not isinstance(returned_output, CalculationLayerResult):

                    # Make sure we are actually dealing with the object we expect.
                    raise ValueError('The output of the calculation was not '
                                     'a CalculationLayerResult as expected.')

                if returned_output.exception is not None:
                    # If an exception was raised, make sure to add it to the list.
                    server_request.exceptions.append(returned_output.exception)

                else:

                    # Make sure to store any important calculation data if no exceptions
                    # were thrown.
                    if (returned_output.data_directories_to_store is not None and
                        returned_output.calculated_property is not None):

                        for data_directory in returned_output.data_directories_to_store:

                            data_file = path.join(data_directory, 'data.json')

                            # Make sure the data directory / file to store actually exists
                            if not path.isdir(data_directory) or not path.isfile(data_file):
                                logging.info(f'Invalid data directory ({data_directory}) / file ({data_file})')
                                continue

                            # Attach any extra metadata which is missing.
                            with open(data_file, 'r') as file:

                                data_object = json.load(file, cls=TypedJSONDecoder)

                                if data_object.force_field_id is None:
                                    data_object.force_field_id = server_request.force_field_id

                            with open(data_file, 'w') as file:
                                json.dump(data_object, file, cls=TypedJSONEncoder)

                            substance_id = data_object.substance.identifier
                            storage_backend.store_simulation_data(substance_id, data_directory)

                matches = [x for x in server_request.queued_properties if x.id == returned_output.property_id]

                for match in matches:
                    server_request.queued_properties.remove(match)

                if len(matches) > 1:
                    raise ValueError(f'A property id ({returned_output.property_id}) conflict occurred.')

                elif len(matches) == 0:

                    logging.info('A calculation layer returned results for a property not in the '
                                 'queue. This sometimes and expectedly occurs when using queue based '
                                 'calculation backends, but should be investigated.')

                    continue

                if returned_output.calculated_property is None and returned_output.exception is None:

                    logging.info('A calculation layer did not return an estimated property nor did it'
                                 'raise an Exception. This sometimes and expectedly occurs when using '
                                 'queue based calculation backends, but should be investigated.')

                    continue

                if returned_output.calculated_property is None:
                    # An exception has been recorded above, but for some reason no property has
                    # been associated with it.
                    continue

                substance_id = returned_output.calculated_property.substance.identifier

                if returned_output.exception is None:

                    if substance_id not in server_request.estimated_properties:
                        server_request.estimated_properties[substance_id] = []

                    server_request.estimated_properties[substance_id].append(returned_output.calculated_property)

                else:

                    if substance_id not in server_request.unsuccessful_properties:
                        server_request.unsuccessful_properties[substance_id] = []

                    server_request.unsuccessful_properties[substance_id].append(returned_output.calculated_property)

        except Exception as e:

            logging.info(f'Error processing layer results for request {server_request.id}')

            formatted_exception = traceback.format_exception(None, e, e.__traceback__)

            exception = PropertyEstimatorException(message='An unhandled internal exception '
                                                           'occurred: {}'.format(formatted_exception))

            server_request.exceptions.append(exception)

        callback(server_request)

    @staticmethod
    def schedule_calculation(calculation_backend, storage_backend, layer_directory,
                             data_model, callback, synchronous=False):
        """Submit the proposed calculation to the backend of choice.

        Parameters
        ----------
        calculation_backend: PropertyEstimatorBackend
            The backend to the submit the calculations to.
        storage_backend: PropertyEstimatorStorage
            The backend used to store / retrieve data from previous calculations.
        layer_directory: str
            The local directory in which to store all local, temporary calculation data from this layer.
        data_model: PropertyEstimatorServer.ServerEstimationRequest
            The data model encoding the proposed calculation.
        callback: function
            The function to call when the backend returns the results (or an error).
        synchronous: bool
            If true, this function will block until the calculation has completed.
            This is mainly intended for debugging purposes.
        """
        raise NotImplementedError()
