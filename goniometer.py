# import os
# import time
# import sys
import clr
from PyQt6.QtCore import QTimer

clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\ThorLabs.MotionControl.IntegratedStepperMotorsCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.Benchtop.PiezoCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericPiezoCLI.dll")

from Thorlabs.MotionControl.DeviceManagerCLI import * # type: ignore
from Thorlabs.MotionControl.GenericMotorCLI import * # type: ignore
from Thorlabs.MotionControl.IntegratedStepperMotorsCLI import * # type: ignore
from System import Decimal  # type: ignore
from Thorlabs.MotionControl.Benchtop.PiezoCLI import * # type: ignore
from Thorlabs.MotionControl.Benchtop.PiezoCLI.PDXC2 import * # type: ignore
from Thorlabs.MotionControl.GenericPiezoCLI.Piezo import * # type: ignore

class GonioPiezo:
    def __init__(self, gnm2_serial, gnm3_serial, pdxc2_serial):
        DeviceManagerCLI.BuildDeviceList() # type: ignore

        self.gnm2 = Goniometer.CreateGoniometer(gnm2_serial) # type: ignore
        self.gnm3 = Goniometer.CreateGoniometer(gnm3_serial) # type: ignore
        self.pdxc2 = InertiaStageController.CreateInertiaStageController(pdxc2_serial) # type: ignore
        self.serials = {'gnm2': gnm2_serial, 'gnm3': gnm3_serial, 'pdxc2': pdxc2_serial}
        QTimer.singleShot(250, self._poll_devices)

    def _poll_devices(self):
        self.gnm2.StartPolling(250)
        self.gnm3.StartPolling(250)
        self.pdxc2.StartPolling(250)
        QTimer.singleShot(250, self._enable_devices)

    def _enable_devices(self):
        self.gnm2.EnableDevice()
        self.gnm3.EnableDevice()
        self.pdxc2.EnableDevice()
    
    def _get_config(self):
        load_gnm2 = self.device.LoadMotorConfiguration(self.serials['gnm2'])
        gnm2_config = self.device.GetMotorConfiguration(self.serials['gnm2'], DeviceConfiguration.DeviceSettingsUseOptionType.UseDeviceSettings) # type: ignore
        
        load_gnm3 = self.device.LoadMotorConfiguration(self.serials['gnm3'])
        gnm3_config = self.device.GetMotorConfiguration(self.serials['gnm3'], DeviceConfiguration.DeviceSettingsUseOptionType.UseDeviceSettings) # type: ignore

        self.device.SetPositionControlMode(PiezoControlModeTypes.CloseLoop) # type: ignore
        pdxc2_config = self.device.GetPDXC2Configuration(self.serials['pdxc2'], DeviceConfiguration.DeviceSettingsUseOptionType.UseDeviceSettings) # type: ignore
    
    def check_enabled(self):
        gnm2_en = self.gnm2.IsEnabled()
        gnm3_en = self.gnm3.IsEnabled()
        pdxc2_en = self.pdxc2.IsEnabled()
        return {'gnm2': gnm2_en, 'gnm3': gnm3_en, 'pdxc2': pdxc2_en}
    
    def set_home(self):
        # 5 seconds timeout
        self.gnm2.Home(5000)
        self.gnm3.Home(5000)
        self.pdxc2.Home(5000)

    def set_pos(self, roll: float, pitch: float, yaw: float):
        self.gnm2.MoveTo(Decimal(pitch), 2000)
        self.gnm3.MoveTo(Decimal(yaw), 2000)
        self.pdxc2.SetClosedLoopTarget(roll)
        self.pdxc2.MoveStart()

    def stop_disconnect(self):
        self.gnm2.StopPolling()
        self.gnm3.StopPolling()
        self.pdxc2.StopPolling()

        self.gnm2.Disconnect()
        self.gnm3.Disconnect()
        self.pdxc2.Disconnect()

if __name__=="__main__":
    gunyo = GonioPiezo()