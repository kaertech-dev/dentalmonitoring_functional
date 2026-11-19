from pylablib.devices import Thorlabs
from PyQt6.QtCore import QThread, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

# QObject inside QThread
class GWorker(QObject):
    start_moving = pyqtSignal(float, float, float)
    start_homing = pyqtSignal()
    moving_finished = pyqtSignal()
    homing_finished = pyqtSignal()
    
    def __init__(self, serial_dict: dict):
        super().__init__()
        self.pdxc2 = Thorlabs.KinesisPiezoController(serial_dict.get('pdxc2'))
        self.gnm2 = Thorlabs.KinesisMotor(serial_dict.get('gnm2'))
        self.gnm3 = Thorlabs.KinesisMotor(serial_dict.get('gnm3'))

        self.start_moving.connect(self.move_to)
        self.start_homing.connect(self.set_home)

    def set_home(self):
        self.pdxc2._home()
        self.gnm2._home()
        self.gnm3._home()

        self.pdxc2._wait_for_home()
        self.gnm2._wait_for_home()
        self.gnm3._wait_for_home()

        self.homing_finished.emit()

    def move_to(self, roll, pitch, yaw):
        self.pdxc2._move_to(roll)
        self.gnm2._move_to(pitch)
        self.gnm3._move_to(yaw)

        self.pdxc2._wait_move()
        self.gnm2._wait_move()
        self.gnm3._wait_move()

        self.moving_finished.emit()

    def get_pos(self):
        return {'pdxc2': self.pdxc2._get_position(), 'gnm2': self.gnm2._get_position(), 'gnm3': self.gnm3._get_position()}

    def get_status(self):
        return {'pdxc2': self.pdxc2._get_status(), 'gnm2': self.gnm2._get_status(), 'gnm3': self.gnm3._get_status()}

    def stop_close(self):
        self.pdxc2.close()
        self.gnm2.close()
        self.gnm3.close()

# Thread+Worker wrapper
class Goniometer(QWidget):
    done_move = pyqtSignal()
    done_home = pyqtSignal()

    def __init__(self, serial_dict: dict):
        super().__init__()
        self.gthread = QThread()
        
        self.worker = GWorker(serial_dict)
        self.worker.moveToThread(self.gthread)
        self.worker.moving_finished.connect(self.done_move.emit)
        self.worker.homing_finished.connect(self.done_home.emit)

        self.gthread.started.connect(self.worker.set_home)
        self.gthread.run()

    def getPos(self):
        return self.worker.get_pos()

    def setHome(self):
        # self.worker.set_home()
        self.worker.start_homing.emit()

    def setPos(self, roll, pitch, yaw):
        # self.worker.move_to(roll, pitch, yaw)
        self.worker.start_moving.emit(roll, pitch, yaw)

    def getStatus(self):
        return self.worker.get_status()

    def disconnect(self):
        self.worker.stop_close()

if __name__=="__main__":
    import sys
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')

    Thorlabs.list_kinesis_devices()
    gonio = Goniometer({'pdxc': '112000001', 'gnm2': '115000002', 'gnm3': '115000003'})

    sys.exit(app.exec())