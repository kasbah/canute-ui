'''
This module defines the DriverBoth class that combines the emulated and real
driver (Pi) allowing you to run them at the same time. Methods are sent to both
drivers but only return values from the emulated one. This means, for instance,
that only the button presses from the emulated GUI are registered.
'''
import logging
log = logging.getLogger(__name__)

from driver import Driver
from driver_emulated import Emulated
from driver_pi import Pi
import utility

class DriverBoth():
    def __init__ (self, port='/dev/ttyACM0', pi_buttons=False, delay=0, display_text=False):
        log.debug('__init__')
        self.emulated = Emulated(delay, display_text)
        self.pi = Pi(port, pi_buttons)
        self.chars = 40
        self.rows = 9
    def __exit__ (self, ex_type, ex_value, traceback):
        pass
    def __enter__(self):
        '''method required for using the `with` statement'''
        return self

defined_methods = utility.get_methods(DriverBoth)
for method_name in utility.get_methods(Driver):
    if method_name not in defined_methods:
        def make_method (method_name):
            def method(self, *args, **kwargs):
                emulated_method = self.emulated.__getattribute__(method_name)
                pi_method = self.pi.__getattribute__(method_name)
                emulated_return = emulated_method(*args, **kwargs)
                pi_method(*args, **kwargs)
                return emulated_return
            return method
        setattr(DriverBoth, method_name, make_method(method_name))
