import os
import re
import sys
import csv
import time
import json
import serial
import pymysql
import subprocess
import configparser
from datetime import datetime

from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QDialog, QMessageBox, QInputDialog, QLineEdit
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence, QStandardItemModel, QStandardItem
from PyQt6.QtCore import QTimer, Qt

from database import DatabaseConn
from new_helper import get_path, SettingsBox, HTTPRequest, StackedUI

Q_OK = QMessageBox.StandardButton.Ok
Q_YES = QMessageBox.StandardButton.Yes
Q_NO = QMessageBox.StandardButton.No

class TestResults:
    def __init__(self, serial_num: str, po_num: str, test_rep: int):
        self.serial_num = serial_num
        self.po_num = po_num
        self.headers = ['serial_num', 'po_num', 'operator_en', 'shift', 'date_time', 'tester_code',
                        'bt_mac', 'ip_address', 'fw_version', 'batt_mv', 
                        'ui_red', 'ui_green', 'ui_blue', 'ui_orange', 
                        'df_lumi', 'df_snr', 'led_white',
                        'lf80_reso', 'lf80_contrast', 'lf80_r2g', 'lf80_b2g', 'lf80_snr', 'lf80_dotgrid',
                        'lf110_reso', 'lf110_contrast', 'lf110_r2g', 'lf110_b2g', 'lf110_snr', 'lf110_dotgrid',
                        'streaming', 'chrg_curr',
                        'duration', 'test_rep', 'status', 'fail_reason'
                        ]

        self.temp_fail_reasons = []
        self.temp_cam_params = {'center': {'dft': False, 'lft_80': False, 'lft_110': False, 'stream': False}}

        self.values = {}
        for col in self.headers:
            self.values[col] = None

        self.values['serial_num'] = serial_num
        self.values['po_num'] = po_num
        now = datetime.now()
        self.values['date_time'] = now.strftime(r"%Y-%m-%d %H:%M:%S")
        self.values['test_rep'] = test_rep

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value

    def exit_fail(self, fail_reason: str):
        self.temp_fail_reasons.append(fail_reason)

    def process_values(self, operator_en: str, shift: str, tester_code: str, test_duration: float) -> dict:
        self.values['operator_en'] = operator_en
        self.values['shift'] = shift
        self.values['tester_code'] = tester_code
        self.values['duration'] = f"{test_duration:.2f}"

        if len(self.temp_fail_reasons) > 0:
            self.values['status'] = 0
            self.values['fail_reason'] = ', '.join(self.temp_fail_reasons)
        else:
            self.values['status'] = 1

        return self.values

    def get_headers(self):
        return self.headers

class MainWindow(QMainWindow):
    def __init__(self, version: str):
        super().__init__()
        uic.loadUi(get_path('layout/functional_test.ui'), self)
        kaertech_logo = QImage(get_path(r"static/kaertech_logo.png")).scaled(90, 90, transformMode=Qt.TransformationMode.SmoothTransformation)
        self.logo_left.setPixmap(QPixmap.fromImage(kaertech_logo))
        dm_logo = QImage(get_path(r"static/dental_logo.png")).scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
        self.logo_right.setPixmap(QPixmap.fromImage(dm_logo))
        self.version.setText(version)

        self.tableModel = QStandardItemModel()
        self.tableView.setModel(self.tableModel)
        
        # Settings and limits files
        self.config = self.load_config()
        self.test_limits = self.load_limits()
        self.dbconn = DatabaseConn(self.config['database'])

        # self.init_test_variables()
        self.connect_PSupply()

        # buttons
        # self.login_button.clicked.connect(self.login)
        self.start_test_button.clicked.connect(self.start_test)
        self.serialno_display.returnPressed.connect(self.validate_serial)
        self.settings_button.clicked.connect(self.open_settings)
        # self.abort_button.clicked.connect(self.abort_signal)
        # self.initialize_shortcuts()

    # def login(self):
    #     if self.login_button.text() == 'LOGIN':
    #         user = self.oprtr_en_input.text().strip().upper()
    #         shift = self.shift_input.currentText().strip().upper()

    #         if user and shift:
    #             # self.login_details = {'operator_en': user, 'shift': shift}
    #             self.oprtr_en_input.setEnabled(False)
    #             self.shift_input.setEnabled(False)
    #             self.login_button.setText('LOGOUT')
    #         else:
    #             QMessageBox.information(self, 'Info', 'Please enter all details', Q_OK)
    #     else:
    #         # self.login_details = {'operator_en': '', 'shift': ''}
    #         self.oprtr_en_input.setEnabled(True)
    #         self.shift_input.setEnabled(True)
    #         self.login_button.setText('LOGIN')

    def open_settings(self):
        mode_box = SettingsBox(self.oprtr_display.text(), self.shift_display.text())
        result = mode_box.exec()

        if result:
            self.oprtr_display.setText(mode_box.output['operator_en'])
            self.shift_display.setText(mode_box.output['shift'])
            if mode_box.output['mode'] == 'DEVELOPMENT':
                self.mode_label.setText('DEVELOPMENT')
                self.mode_label.setStyleSheet("background-color: rgb(170, 255, 255);")
            else:
                self.mode_label.setText('PRODUCTION')
                self.mode_label.setStyleSheet("background-color: rgb(255, 255, 0);")
            
            # self.load_config()
            # self.init_test_variables()

    def load_config(self) -> dict:
        parser = configparser.ConfigParser()
        parser.read('Settings.ini')
        config = {}

        for key, raw_value in parser['Settings'].items():
            try:
                value = int(raw_value.strip('"'))
            except ValueError:
                value = raw_value.strip('"')
                if value == 'None':
                    value = ''
            config[key.lower().replace(' ', '_')] = value

        if 'Database' in parser.sections():
            db_host = parser.get('Database', 'Host')
            db_schema = parser.get('Database', 'Customer')
            db_model = parser.get('Database', 'Model')
            db_station = parser.get('Database', 'Station')

            config['database'] = {'host': db_host, 'customer': db_schema, 'model': db_model, 'station': db_station}
            self.model_display.setText(db_model)
        else:
            config['database'] = None

        if 'Camera' in parser.sections():
            cam_src = parser.getint('Camera', 'Source')
            cam_exp = parser.getint('Camera', 'Exposure')
            cam_focus = parser.getint('Camera', 'Focus')
            cam_zoom = parser.getint('Camera', 'Zoom')
            config['camera'] = {'source': cam_src, 'exposure': cam_exp, 'focus': cam_focus, 'zoom': cam_zoom}
        else:
            config['camera'] = {'source': 0}

        # if db_model == 'S3':
        #     try:
        #         from goniometer import GonioPiezo
        #         self.goniometer = GonioPiezo(config['gnm2_serial'], config['gnm3_serial'], config['pdxc2_serial'])
        #         self.load_s3_calib()
        #     except:
        #         self.messages.setText('GNM2, GNM3, PDXC2 not detected')

        return config

    def load_limits(self):
        filename = self.config['limits_path'] or 'limits.json'
        with open(filename, 'r') as limits_file:
            found = json.load(limits_file)

        return found

    def connect_PSupply(self):
        try:
            self.hw_serial = serial.Serial(port = self.config['hw_port'], baudrate = int(self.config['baud_rate']))
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.messages.setText(f'POWER SUPPLY AT {self.config["hw_port"]}')
        except:
            QMessageBox.information(self, 'No Supply', 'Power Supply not detected', Q_OK)
            self.hw_serial = None

    def enable_PSupply(self, state: bool):
        if not self.hw_serial:
            return

        self.hw_serial.reset_input_buffer()
        self.hw_serial.reset_output_buffer()
        if state:
            self.hw_serial.write(b'OUT1\r\n')
        else:
            self.hw_serial.write(b'OUT0\r\n')
        self.hw_serial.flush()

    def read_current(self):
        if not self.hw_serial or not self.hw_serial.is_open:
            self.stacker.opcurr_display.setText("Disconnected")
            return None
        
        self.hw_serial.reset_input_buffer()
        self.hw_serial.reset_output_buffer()
        self.hw_serial.write(b'IOUT2?\r\n')
        self.hw_serial.flush()
        response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
        if not response:
            response = self.hw_serial.read_until(expected=b'\n', size=100).decode('ascii', errors='ignore').strip()

        match = re.search(r'[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?', response)
        if not match:
            self.stacker.opcurr_display.setText("No Reading")
            return None

        curr_value = float(match.group(0)) * 1000
        self.stacker.opcurr_display.setText(f"{curr_value:.3f} mA")
        return curr_value

    def clear_table(self):
        self.start_test_button.setEnabled(False)
        self.abort_button.setEnabled(False)

        # self.current_serial = ''
        # self.mac_address = ''

        # self.head_details = {}
        # self.bringup_results = {}
        # self.tail_details = {}
        # self.fail_reasons = []

        self.tableModel.clear()
        self.tableModel.setHorizontalHeaderLabels(['Test', 'Low', 'Value', 'High', 'Status', 'Fail Reason'])
        self.tableView.setColumnWidth(0, 120)
        self.tableView.setColumnWidth(1, 120)
        self.tableView.setColumnWidth(2, 120)
        self.tableView.setColumnWidth(3, 120)
        self.tableView.setColumnWidth(4, 70)
        self.tableView.setColumnWidth(5, 170)

    def add_row(self, param_name: str, value: str, status: str, low: str = '', high: str = '', fail_reason: str = ''):
        test_item = QStandardItem(param_name)
        low_item = QStandardItem(low)
        value_item = QStandardItem(value)
        high_item = QStandardItem(high)
        status_item = QStandardItem(status)
        fail_item = QStandardItem(fail_reason)
        self.tableModel.appendRow([test_item, low_item, value_item, high_item, status_item, fail_item])

        if not fail_reason:
            self.final_results[param_name] = value
        else:
            self.final_results.exit_fail(fail_reason)

    def validate_serial(self):
        try:
            user = self.oprtr_display.text()
            shift = self.shift_display.text()
            if not (user and shift):
                # if self.mode_label.text() == 'PRODUCTION':
                QMessageBox.information(self, 'Info', 'Please login to start testing', buttons=QMessageBox.StandardButton.Ok)
                QTimer.singleShot(10, self.open_settings)
                self.serialno_display.setText('')
                return False
            
            self.clear_table()

            self.current_serial = self.serialno_display.text().strip().upper()
            if not self.current_serial:
                QMessageBox.information(self, 'Info', 'Enter serial number to test', buttons=QMessageBox.StandardButton.Ok)
                self.serialno_display.setFocus()
                return False

            db_status = self.dbconn.get_db_status(self.current_serial)
            if 'error' in db_status:
                QMessageBox.warning(self, 'Error', db_status['error'], buttons=QMessageBox.StandardButton.Ok)
                if self.mode_label.text() == 'PRODUCTION':
                    return False

            po_num = db_status.get('po_num', '')
            self.ponum_display.setText(po_num)
            test_rep = db_status.get('test_rep', 1)

            self.final_results = TestResults(self.current_serial, po_num, test_rep)
            self.start_test_button.setEnabled(True)

        except Exception as e:
            QMessageBox.warning(self, 'Exception occurred', str(e), Q_OK)
    
    def start_test(self):
        try:
            if not self.hw_serial:
                res = QMessageBox.critical(self, 'Error', 'Power Supply not detected', Q_OK)
                # return

            self.enable_PSupply(False)
            # self.enable_buttons(False)

            # oprtr = self.oprtr_en_input.text().strip().upper()
            # shift = self.shift_input.currentText().strip().upper()
            # tester_code = self.config['tester_code']
            # self.final_results.op_login(oprtr, shift, tester_code)

            self.stacker = StackedUI()

            # self.stacker.done_measure.connect(self.ft_measure_end)
            # self.stacker.done_stream.connect(self.close_stream)
            self.messages.setText("Test in progress")
            self.start_test_time = time.time()

            QTimer.singleShot(1, self.get_device_mac)
        except Exception as e:
            QMessageBox.warning(self, 'Exception occurred', str(e), Q_OK)

    def get_device_mac(self):
        try:
            result = self.dbconn.get_ble_mac(self.current_serial)
            if 'error' in result:
                QMessageBox.critical(self, "Error", result['error'])
                if self.mode_label.text() == 'PRODUCTION':
                    self.add_row('bt_mac', '', 'FAIL', fail_reason='NO_BLE')
                    # self.final_results.exit_fail('NO_BLE')
                    return

            self.mac_address = result.get('bt_mac', 'CC:C8:37:2F:8B:29')
            # self.final_results['bt_mac'] = self.mac_address
            self.add_row('bt_mac', self.mac_address, 'PASS')

            self.stacker.found_ip.connect(self.insert_ip)
            self.stacker.dev_status.connect(self.insert_status)
            self.stacker.show()
            self.stacker.dut_ap_connect(self.mac_address)

        except Exception as e:
            QMessageBox.warning(self, 'Exception occurred', str(e), Q_OK)

    # ========== Step 0. BLE & WiFi Connection ========== #
    def insert_ip(self, ipaddress):
        self.add_row('ip_address', ipaddress, 'PASS')

    def insert_status(self, received: dict):
        # for key, value in received.items():
        #     self.add_row(key, value, 'PASS')
        fw_ver = received.get('firmware_version')
        batt_mv = received.get('battery_voltage_mv')
        self.add_row('fw_version', fw_ver, 'PASS')
        self.add_row('batt_mv', batt_mv, 'PASS')

        QTimer.singleShot(10, )

    def write_csv(self):
        now = datetime.now()
        if self.mode_label.text() == 'DEVELOPMENT':
            dayfile = f"DEV/results_{self.device_model}_{now.date().isoformat()}.csv"
        else:
            dayfile = f"PROD/results_{self.device_model}_{now.date().isoformat()}.csv"
        if not os.path.exists(dayfile):
            with open(dayfile, 'w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=self.csv_headers, extrasaction='ignore')
                writer.writeheader()
    
        with open(dayfile, 'a', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=self.csv_headers, extrasaction='ignore')
            final = self.head_details | self.bringup_results | self.tail_details
            writer.writerow(final)

if __name__=='__main__':
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')

    window = MainWindow('v1.3.0')
    window.show()

    # Start the event loop.
    sys.exit(app.exec())