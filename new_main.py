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
from PyQt6.QtWidgets import QApplication, QMainWindow, QDialog, QMessageBox
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence, QStandardItemModel, QStandardItem
from PyQt6.QtCore import QTimer, Qt

from database import DatabaseConn
from new_helper import get_path, HTTPRequest, StackedUI

class SettingsBox(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi(get_path('layout/settings_box.ui'), self)
        self.dev_button.clicked.connect(self.set_dev)
        self.prod_button.clicked.connect(self.set_prod)
        self.mode = ''

    def set_dev(self):
        self.mode = 'DEVELOPMENT'
        self.accept()

    def set_prod(self):
        self.mode = 'PRODUCTION'
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi(get_path('layout/functional_test.ui'), self)
        kaertech_logo = QImage(get_path(r"static/kaertech_logo.png")).scaled(90, 90, transformMode=Qt.TransformationMode.SmoothTransformation)
        self.logo_left.setPixmap(QPixmap.fromImage(kaertech_logo))
        dm_logo = QImage(get_path(r"static/dental_logo.png")).scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
        self.logo_right.setPixmap(QPixmap.fromImage(dm_logo))
        
        # Settings and limits files
        self.config = {}
        self.load_config()
        self.login_details = {'operator_en': '', 'shift': ''}

        # buttons
        self.login_button.clicked.connect(self.login)
        self.start_test_button.clicked.connect(self.start_test)
        # self.serialno_display.returnPressed.connect(self.validate_serial)
        self.settings_button.clicked.connect(self.open_settings)
        # self.abort_button.clicked.connect(self.abort_signal)
        # self.initialize_shortcuts()

    def login(self):
        if self.login_button.text() == 'LOGIN':
            user = self.oprtr_en_input.text().strip().upper()
            shift = self.shift_input.currentText().strip().upper()

            if user and shift:
                self.login_details = {'operator_en': user, 'shift': shift}
                self.oprtr_en_input.setEnabled(False)
                self.shift_input.setEnabled(False)
                self.login_button.setText('LOGOUT')
            else:
                QMessageBox.information(self, 'Info', 'Please enter all details', buttons=QMessageBox.StandardButton.Ok)
        else:
            self.login_details = {'operator_en': '', 'shift': ''}
            self.oprtr_en_input.setEnabled(True)
            self.shift_input.setEnabled(True)
            self.login_button.setText('LOGIN')

    def open_settings(self):
        mode_box = SettingsBox()
        result = mode_box.exec()

        if result:
            if mode_box.mode == 'DEVELOPMENT':
                self.mode_label.setText('DEVELOPMENT')
                self.mode_label.setStyleSheet("background-color: rgb(170, 255, 255);")
            else:
                self.mode_label.setText('PRODUCTION')
                self.mode_label.setStyleSheet("background-color: rgb(255, 255, 0);")

    def load_config(self):
        parser = configparser.ConfigParser()
        parser.read('Settings.ini')

        for key, raw_value in parser['Settings'].items():
            try:
                value = int(raw_value.strip('"'))
            except ValueError:
                value = raw_value.strip('"')
                if value == 'None':
                    value = ''
            self.config[key.lower().replace(' ', '_')] = value

        if 'Database' in parser.sections():
            db_host = parser.get('Database', 'Host')
            db_schema = parser.get('Database', 'Customer')
            db_model = parser.get('Database', 'Model')
            db_station = parser.get('Database', 'Station')

            self.config['database'] = {'host': db_host, 'customer': db_schema, 'model': db_model, 'station': db_station}
            self.dbconn = DatabaseConn(db_host, db_schema, db_model, db_station)
            if 'S3' in db_model and 'M1' not in db_model:
                self.device_model = 'S3'
            else:
                self.device_model = 'M1'
            self.model_display.setText(db_model)
        else:
            self.config['database'] = None
            self.dbconn = None

        if 'Camera' in parser.sections():
            cam_src = parser.getint('Camera', 'Source')
            cam_exp = parser.getint('Camera', 'Exposure')
            cam_focus = parser.getint('Camera', 'Focus')
            cam_zoom = parser.getint('Camera', 'Zoom')
            self.config['camera'] = {'source': cam_src, 'exposure': cam_exp, 'focus': cam_focus, 'zoom': cam_zoom}
        else:
            self.config['camera'] = {'source': 0}

        if self.device_model == 'S3':
            try:
                from goniometer import GonioPiezo
                self.goniometer = GonioPiezo(self.config['gnm2_serial'], self.config['gnm3_serial'], self.config['pdxc2_serial'])
                self.load_s3_calib()
            except:
                self.messages.setText('GNM2, GNM3, PDXC2 not detected')

    def init_test_variables(self):
        pass
    
    def start_test(self):
        try:
            # self.forget_connection()
            if not self.hw_serial:
                res = QMessageBox.critical(self, 'Error', 'Power Supply not detected', QMessageBox.StandardButton.Ok)
                # return
            
            if self.current_serial == '':
                val = self.validate_serial()
                if not val:
                    return

            self.stacker.reset_holders()
            resp_off = subprocess.run(self.relay_off_cmd)
            self.head_details['serial_num'] = self.current_serial
            self.head_details['po_num'] = self.ponum_display.text()
            if len(self.config['tester_code']) > 0:
                self.head_details['tester_code'] = self.config['tester_code']
            self.head_details.update(self.login_details)
            self.enable_buttons(False)

            try:
                self.stacker.done_measure.disconnect()
                self.stacker.done_stream.disconnect()
            except TypeError:
                pass

            self.stacker.done_measure.connect(self.ft_measure_end)
            self.stacker.done_stream.connect(self.close_stream)
            self.messages.setText("Test in progress")
            self.start_test_time = time.time()

            self.camera_list = ['center']
            if self.device_model == 'S3':
                self.camera_list = ['center', 'right', 'left']
            
            now = datetime.now()
            self.head_details['date_time'] = now.strftime(r"%Y-%m-%d %H:%M:%S")
            QTimer.singleShot(1, self.get_device_mac)
        except:
            pass

if __name__=='__main__':
    # CLI_PREFIX = os.getenv('CLI_PREFIX', 'C:\\Users\\ai\\')
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')
    window = MainWindow()
    window.show()

    # Start the event loop.
    sys.exit(app.exec())