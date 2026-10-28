import os
import re
import sys
import csv
import time
import json
import serial
import subprocess
import configparser
from datetime import datetime

from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence, QStandardItemModel, QStandardItem
from PyQt6.QtCore import QTimer, Qt

from database import DatabaseConn
from helper_classes import HTTPRequest, SettingsBox, StackedUI

import logging
logger = logging.getLogger(__name__)

def get_path(raw_path):
    try:
        ret = os.path.join(sys._MEIPASS, raw_path)
    except AttributeError:
        ret = os.path.join('.', raw_path)
    return ret

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi(get_path('layout/functional_test.ui'), self)
        kaertech_logo = QImage(get_path(r"static/kaertech_logo.png")).scaled(90, 90, transformMode=Qt.TransformationMode.SmoothTransformation)
        self.logo_left.setPixmap(QPixmap.fromImage(kaertech_logo))
        dm_logo = QImage(get_path(r"static/dental_logo.png")).scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
        self.logo_right.setPixmap(QPixmap.fromImage(dm_logo))

        self.config = {}
        self.goniometer = None
        self.s3_calib_poses = {}
        self.csv_headers = ['serial_num', 'po_num', 'operator_en', 'shift', 'date_time', 'tester_code',
                            'ip_address', 'fw_version', 'batt_mv', 'ui_red', 'ui_green', 'ui_blue', 'ui_orange', 
                            'df_lumi', 'df_snr', 'led_white',
                            'lf80_reso', 'lf80_contrast', 'lf80_r2g', 'lf80_b2g', 'lf80_snr', 'lf80_dotgrid',
                            'lf110_reso', 'lf110_contrast', 'lf110_r2g', 'lf110_b2g', 'lf110_snr', 'lf110_dotgrid',
                            'streaming', 'chrg_curr',
                            'duration', 'test_rep', 'status', 'fail_reason'
                            ]
        self.login_details = {'operator_en': '', 'shift': 'A'}
        self.load_config()
        self.load_limits()

        self.tableModel = QStandardItemModel()
        self.tableView.setModel(self.tableModel)
        self.init_test_variables()

        # self.relay_off_cmd = ['daq/TSD_DAQ.exe', '--port', 'D',  '--execute', '0']
        # res = subprocess.run(self.relay_off_cmd)
        # self.relay_on_cmd = ['daq/TSD_DAQ.exe', '--port', 'D',  '--execute', '1']

        self.auto_abort_timer = QTimer()
        self.auto_abort_timer.setInterval(1000 * 30)
        self.auto_abort_timer.timeout.connect(lambda: self.add_fail_reason('Test timeout'))

        self.golden_tested_flag = None
        self.golden_sn = None
        
        if self.device_model == 'S3':
            self.bar_step = 100 / 20
        else:
            self.bar_step = 100 / 12
        self.start_test_button.setEnabled(False)

        self.http_manager = HTTPRequest(self)
        self.stacker = StackedUI(10, self.config['cam_source'], self.config['cam_params'], 
                                 self.test_limits, self.config['dut_ssid'], self.config['dut_password'])
        self.stacker.fail_reason.connect(self.add_fail_reason)
        self.stacker.alt_signal.connect(self.alt_meas_end)
        self.stacker.led_okay.connect(self.led_catcher)

        self.hw_timer = QTimer(self)
        self.hw_timer.timeout.connect(self.update_current)
        self.connect_port()

        # timer = QTimer(self)
        # timer.timeout.connect(self.update_time)
        # timer.start(1000)
        # self.update_time()

        # Connect button click signal to slot
        self.login_button.clicked.connect(self.login)
        self.start_test_button.clicked.connect(self.start_test)
        self.serialno_display.returnPressed.connect(self.validate_serial)
        self.settings_button.clicked.connect(self.open_settings)
        self.abort_button.clicked.connect(self.abort_signal)

        # Keyboard shortcuts
        self.initialize_shortcuts()

    # def update_time(self):
    #     now = datetime.now()
    #     self.date_display.setText(now.date().strftime(r'%m/%d/%Y'))
    #     self.time_display.setText(now.time().strftime(r'%I:%M:%S %p'))
    
    def initialize_shortcuts(self):
        SC_start_test = QShortcut(QKeySequence("F12"), self)
        SC_start_test.activated.connect(self.start_test)

        SC_open_settings = QShortcut(QKeySequence("F10"), self)
        SC_open_settings.activated.connect(self.open_settings)

        SC_abort_test = QShortcut(QKeySequence("F8"), self)
        SC_abort_test.activated.connect(self.abort_signal)

        SC_exit_app = QShortcut(QKeySequence("End"), self)
        SC_exit_app.activated.connect(self.close)
    
    def login(self):
        if self.login_button.text() == 'LOGIN':
            user = self.oprtr_en_input.text()
            shift = self.shift_input.currentText()

            if user and shift:
                self.login_details['operator_en'] = user.strip().upper()
                self.login_details['shift'] = shift.strip().upper()
                self.oprtr_en_input.setEnabled(False)
                self.shift_input.setEnabled(False)
                self.login_button.setText('LOGOUT')
            else:
                QMessageBox.information(self, 'Info', 'Please enter all details', buttons=QMessageBox.StandardButton.Ok)
        else:
            self.login_details['operator_en'] = 'DEV_MODE'
            self.login_details['shift'] = 'A'
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
            
            self.load_config()
            self.init_test_variables()

    def connect_port(self):
        try:
            self.hw_serial = serial.Serial(port = self.config['hw_port'], baudrate = int(self.config['baud_rate']))
            # self.hw_serial.write(b'OUT1\r\n')
            # self.hw_serial.write(b'OUT0\r\n')
            # self.hw_serial.flush()
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.messages.setText(f'POWER SUPPLY AT {self.config["hw_port"]}')
            # self.hw_timer.start(1000)
        except:
            self.hw_serial = None
            self.messages.setText('POWER SUPPLY NOT DETECTED')
            self.messages.setStyleSheet("color: rgb(220, 30, 30);")
            self.stacker.opcurr_display.setText("Not Detected")

    def update_current(self):
        if not self.hw_serial or not self.hw_serial.is_open:
            self.stacker.opcurr_display.setText("Disconnected")
            return

        current = self.read_current()
        if current is not None:
            self.stacker.opcurr_display.setText(f"{current:.3f} mA")
        else:
            self.stacker.opcurr_display.setText("No Reading")

    def read_current(self):
        if not self.hw_serial:
            return None
        try:
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.hw_serial.write(b'IOUT2?\r\n')
            self.hw_serial.flush()
            response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
            if not response:
                response = self.hw_serial.read_until(expected=b'\n', size=100).decode('ascii', errors='ignore').strip()

            match = re.search(r'[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?', response)
            if not match:
                return None

            return float(match.group(0)) * 1000 # output in Amperes, convert to mA for limits
        except Exception:
            self.hw_serial.write(b'ERR?\r\n')
            self.hw_serial.flush()
            response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
            return None

    def enable_supply(self):
        if not self.hw_serial:
            return None
        try:
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.hw_serial.write(b'OUT1\r\n')
            self.hw_serial.flush()
            # response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
        except:
            pass

    def disable_supply(self):
        if not self.hw_serial:
            return None
        try:
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.hw_serial.write(b'OUT0\r\n')
            self.hw_serial.flush()
            # response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
        except:
            pass

    def abort_signal(self):
        self.auto_abort_timer.stop()
        self.stacker.abort_test()
        self.http_manager.abortAll()

        if self.start_test_time:
            test_duration = time.time() - self.start_test_time
            self.duration_display.setText(f"{test_duration:.4f}")
            self.start_test_time = None
        self.enable_buttons(True)
        self.messages.setText('Enter serial number to start testing')
        self.serialno_display.setText("")
        self.serialno_display.setFocus()

    def enable_buttons(self, state: bool):
        if state == False:
            self.start_test_button.setEnabled(state)
        self.login_button.setEnabled(state)
        self.settings_button.setEnabled(state)

    def init_test_variables(self):
        try:
            self.goniometer.set_home()
        except:
            pass
        
        self.current_serial = ''
        self.mac_address = ''
        self.head_details = {'serial_num': '', 'po_num': '', 'operator_en': '', 'shift': '', 'date_time': '', 'tester_code': ''}
        self.tail_details = {'duration': '', 'test_rep': '', 'status': ''}

        self.led_results = {}
        self.cam_results = {}
        self.stream_results = {}
        self.bringup_results = {}
        self.all_results = {}
        self.fail_reasons = []

        self.start_test_time = 0
        self.progress_percent = 0
        self.old_batt_lvl = 0

        self.test_progress.reset()
        self.pass_fail_output.setText("")
        self.pass_fail_output.setStyleSheet("background-color: rgb(180, 180, 180);")
        # self.enable_buttons(True)

        self.tableModel.clear()
        self.tableModel.setHorizontalHeaderLabels(['Test', 'Value', 'Status', 'Fail Reason'])
        self.tableView.setColumnWidth(0, 150)
        # self.tableView.setColumnWidth(1, 100) #
        self.tableView.setColumnWidth(1, 120)
        # self.tableView.setColumnWidth(1, 100) #
        self.tableView.setColumnWidth(2, 70)
        self.tableView.setColumnWidth(3, 405)

    # Add entry to table and final values
    def append_result(self, param_name: str, value, status: str, fail_reason = ''):
        test_item = QStandardItem(param_name)
        value_item = QStandardItem(str(value))
        status_item = QStandardItem(status)
        fail_item = QStandardItem(fail_reason)
        self.tableModel.appendRow([test_item, value_item, status_item, fail_item])

        if status == 'PASS':
            self.all_results[param_name] = 1
        else:
            self.all_results[param_name] = 0
            self.fail_reasons.append(fail_reason)

        self.progress_percent += self.bar_step
        self.test_progress.setValue(round(self.progress_percent))

    # Add entry to table only (not in final values)
    def append_temp(self, param_name: str, value, status: str, fail_reason = ''):
        test_item = QStandardItem('_' + param_name)
        value_item = QStandardItem(str(value))
        status_item = QStandardItem(status)
        fail_item = QStandardItem(fail_reason)
        self.tableModel.appendRow([test_item, value_item, status_item, fail_item])

    def add_fail_reason(self, fr_text: str):
        self.fail_reasons.append(fr_text)
        QTimer.singleShot(10, self.auto_fail)
        self.activateWindow()
        resp = QMessageBox.critical(self, 'Device FAIL', f"Reason code: {fr_text}", QMessageBox.StandardButton.Ok)

    def test_end(self):
        self.enable_buttons(True)
        self.serialno_display.setText('')
        self.serialno_display.setFocus()

    def upload_results(self):
        try:
            self.write_csv()
            if self.mode_label.text() == 'PRODUCTION':
                if self.current_serial == self.golden_sn:
                    self.dbconn.log_golden_test(self.config['tester_code'], self.golden_sn, self.tail_details['status'])
                    self.golden_tested_flag = True
                else:
                    success, error_code = self.dbconn.db_insert(self.head_details, self.bringup_results, self.tail_details)
                    if not success:
                        QMessageBox.critical(self, 'Error', error_code, buttons=QMessageBox.StandardButton.Ok)

            QTimer.singleShot(1, self.test_end)
        except:
            pass

    def auto_fail(self):
        try:
            print('--- auto fail start')
            self.auto_abort_timer.stop()
            self.activateWindow()
            self.test_progress.setValue(100)

            self.unpair_cmd = ['daq/BluetoothDevicePairing.exe', 'unpair-by-mac', '--mac', self.mac_address, '--type', 'BluetoothLE']
            resp_unpair = subprocess.run(self.unpair_cmd)

            # self.stacker.wifi_widget.restored = False
            self.stacker.wifi_widget.forget_network()
            # self.stacker.wifi_widget.restore_net()

            # if self.stacker.wifi_widget.restored:
            #     QTimer.singleShot(10, self.auto_fail_end)
            #     return

            # try:
            #     self.stacker.wifi_widget.net_restored.disconnect()
            # except TypeError:
            #     pass
            # self.stacker.wifi_widget.net_restored.connect(self.auto_fail_end)
        except:
            pass

    def auto_fail_end(self):
        try:
            print('--- auto fail end')
            # try:
            #     self.stacker.wifi_widget.net_restored.disconnect(self.auto_fail_end)
            # except TypeError:
            #     pass

            self.stacker.abort_test()

            test_duration = time.time() - self.start_test_time
            self.duration_display.setText(f"{test_duration:.4f}")
            self.tail_details['duration'] = f"{test_duration:.2f}"
            # self.messages.setText('Test timeout.')

            self.tail_details['status'] = 0
            self.tail_details['fail_reason'] = ','.join(self.fail_reasons)[:1000]
            self.pass_fail_output.setText("FAIL")
            self.pass_fail_output.setStyleSheet("background-color: rgb(245, 0, 0);")

            QTimer.singleShot(1, self.upload_results)
        except:
            pass

    # End of functional test process
    def process_values(self):
        try:
            self.auto_abort_timer.stop()
            self.activateWindow()
            self.test_progress.setValue(100)
            # try:
            #     self.stacker.wifi_widget.net_restored.disconnect(self.upload_results)
            # except TypeError:
            #     pass

            test_duration = time.time() - self.start_test_time
            self.duration_display.setText(f"{test_duration:.4f}")
            self.tail_details['duration'] = f"{test_duration:.2f}"

            if len(self.fail_reasons) == 0:
                self.tail_details['status'] = 1
                self.pass_fail_output.setText("PASS")
                self.pass_fail_output.setStyleSheet("background-color: rgb(20, 230, 20);")
            else:
                self.tail_details['status'] = 0
                self.tail_details['fail_reason'] = ','.join(self.fail_reasons)[:1000]
                self.pass_fail_output.setText("FAIL")
                self.pass_fail_output.setStyleSheet("background-color: rgb(245, 0, 0);")

            # print('head -- ', self.head_details)
            # print('all -- ', self.all_results)
            # print('tail -- ', self.tail_details)
            
            QTimer.singleShot(1, self.upload_results)
        except:
            pass

    def charging_status(self):
        try:
            self.auto_abort_timer.start()
            print('step 8 end')
            curr_meas = self.read_current()
            self.hw_timer.stop()

            if curr_meas:
                self.append_result('chrg_current', curr_meas, 'PASS')
            else:
                self.append_result('chrg_current', -1, 'FAIL', 'CHARGE_FAIL')
            
            self.bringup_results['chrg_curr'] = curr_meas
            # resp_off = subprocess.run(self.relay_off_cmd)
            self.disable_supply()

            # if self.stacker.wifi_widget.restored:
            #     QTimer.singleShot(1, self.process_values)
            # else:
            #     self.stacker.wifi_widget.net_restored.connect(self.process_values)
        except:
            pass

    # Step 8. Charging Test
    def charging_test(self):
        try:
            self.auto_abort_timer.start()
            print('Step 8 start')
            # self.hw_timer.start(1000)
            # resp_on = subprocess.run(self.relay_on_cmd)
            self.enable_supply()
            try:
                self.stacker.done_stream.disconnect(self.close_stream)
            except TypeError:
                pass
            self.stacker.s6_chrg.setChecked(True)

            if self.hw_serial:
                self.hw_timer.start(1000)

            self.stacker.wifi_widget.forget_network()
            # self.stacker.wifi_widget.restore_net()
            QTimer.singleShot(5000, self.charging_status)
        except:
            pass

    # Step 7 end
    # def stream_end(self):
    #     self.auto_abort_timer.start()
    #     if 'STREAM_FAIL'

    #     if self.device_model == 'S3' and len(self.stream_results) < 3:
    #         QTimer.singleShot(1, self.get_stream)
    #         return

    #     self.http_manager.put('white', 
    #                         f'http://{self.device_ip}/torch-mode', 
    #                         'text/plain', 
    #                         'off', 
    #                         self.charging_test,
    #                         100)
        # QTimer.singleShot(1, self.charging_test)
    
    def close_stream(self, curr_cam):
        try:
            self.auto_abort_timer.start()
            idx = len(self.stream_results)
            curr_cam = self.camera_list[idx]
            t_name = curr_cam + '_stream'

            if 'STREAM_FAIL' not in self.fail_reasons:
                self.bringup_results['streaming'] = 1
                self.append_result(t_name, '-', 'PASS')
                
            if self.device_model == 'S3' and len(self.stream_results) < 3:
                QTimer.singleShot(1, self.get_stream)
                return

            self.stacker.wifi_widget.maintain_conn()
            self.http_manager.put('white', 
                                f'http://{self.device_ip}/torch-mode', 
                                'text/plain', 
                                'off', 
                                self.charging_test,
                                100)
        except:
            pass

    # Step 7. Streaming & Connectivity Test
    def get_stream(self):
        self.auto_abort_timer.start()
        idx = len(self.stream_results)
        curr_cam = self.camera_list[idx]
        t_name = curr_cam + '_stream'

        self.stacker.wifi_widget.maintain_conn()

        try:
            stream_link = f'http://{self.device_ip}/preview-stream?camera={curr_cam}'
            self.stacker.onTop()
            self.stacker.stream_start(stream_link)

        except Exception as e:
            resp = QMessageBox.critical(self, 'Error', str(e), QMessageBox.StandardButton.Yes)
            self.stream_results[curr_cam] = 0
            self.append_result(t_name, '-', 'FAIL', 'STREAM_FAIL')
            QTimer.singleShot(10, self.auto_fail)
            return

    # # Step 6.4: Calibrate
    # def retrieve_s3_img(self):
    #     if not self.calib_images['left']:
    #         self.calib_images['left'] = self.http_manager.read_response('left')
    #     if not self.calib_images['right']:
    #         self.calib_images['right'] = self.http_manager.read_response('right')

    #     if self.calib_images['left'] and self.calib_images['right']:
    #         # calibration process
    #         self.pose_index += 1
    #         QTimer.singleShot(1, self.get_s3_img)

    # # Step 6.3: Get Image IDs
    # def read_s3_img(self):
    #     req_id = 'calib_' + self.pose_index
    #     image_ids = self.http_manager.read_response(req_id)
    #     left_id = image_ids['left']
    #     right_id = image_ids['right']

    #     self.calib_images = {'left': None, 'right': None}
    #     self.http_manager.get('left', f'http://{self.device_ip}/photos/{left_id}', self.retrieve_s3_img)
    #     self.http_manager.get('right', f'http://{self.device_ip}/photos/{right_id}', self.retrieve_s3_img)

    # # Step 6.2: Trigger capture
    # def get_s3_img(self):
    #     if self.pose_index == len(self.s3_calib_poses):
    #         QTimer.singleShot(1, self.get_stream)
    #         return

    #     roll, pitch, yaw = self.s3_calib_poses.get(self.pose_index)
    #     self.goniometer.set_pos(roll, pitch, yaw)

    #     req_id = 'calib_' + self.pose_index
    #     self.http_manager.post(req_id, f'http://{self.device_ip}/take-pictures', self.read_s3_img)

    # # Step 6.1: S3 Calibration
    # def s3_calibration(self):
    #     self.pose_index = 0
    #     QTimer.singleShot(1, self.get_s3_img)

    # Step 4 end
    def torch_enabled(self):
        try:
            self.auto_abort_timer.start()
            content = self.http_manager.read_response('white')
            
            res = QMessageBox.information(self, 'Info', 'Please verify if the LEDs are on', buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res == QMessageBox.StandardButton.Yes:
                self.stacker.wifi_widget.maintain_conn()
                self.append_result('led_white', '-', 'PASS')
                self.current_mode = 'lit_80'
                self.http_manager.post('lit_80_frame', 
                                    f'http://{self.device_ip}/take-pictures', 
                                    self.read_image_id,
                                    1500)
            else:
                self.append_result('led_white', '-', 'FAIL', 'LED_FAIL')
                QTimer.singleShot(10, self.auto_fail)
                return
        except:
            pass

    # Step 4: White LED Test
    def torch_test(self):
        try:
            self.auto_abort_timer.start()
            print('--- TORCH ON')
            # increased delay to allow LEDs to turn on
            self.stacker.wifi_widget.maintain_conn()
            self.http_manager.put('white', 
                                f'http://{self.device_ip}/torch-mode', 
                                'text/plain', 
                                'white')
            # self.http_manager.put('fluorescent', f'http://{self.device_ip}/torch-mode', 'text/plain', 'fluorescent')
            QTimer.singleShot(2000, self.torch_enabled)
        except:
            pass

    def led_catcher(self, lumi_val):
        self.bringup_results['led_white'] = round(lumi_val, 3)
        self.append_temp('led_white', round(lumi_val, 3), '-')
        # if lumi_val > float(self.test_limits['led_luminance']['minimum']):
        #     self.append_result('led_white', lumi_val, 'PASS')
        # else:
        #     self.append_result('led_white', lumi_val, 'FAIL', 'LED_FAIL')

    # Routes step 3 to 4, and step 5 to 6
    def next_step(self):
        try:
            self.auto_abort_timer.start()
            print(self.cam_results)
            if len(self.cam_results['lit_80']) < len(self.cam_list):
                # if DF bad = camera issue
                # DF test done, go to LF test
                QTimer.singleShot(1, self.torch_test)
            elif len(self.cam_results['lit_110']) < len(self.cam_list):
                self.stacker.wifi_widget.maintain_conn()
                res = QMessageBox.information(self, 'IMPORTANT', 'Lift the DUT cradle to 110mm position then click OK', QMessageBox.StandardButton.Ok)
                self.current_mode = 'lit_110'
                self.http_manager.post('lit_110_frame', 
                                            f'http://{self.device_ip}/take-pictures', 
                                            self.read_image_id,
                                            1000)
            else:
                # if DF good, LF bad = LED issue
                # LF test done, go to S3 calibration
                
                for name in self.cam_list.keys():
                    dark_ft = self.cam_results['dark'].get(name)
                    lit_80_ft = self.cam_results['lit_80'].get(name)
                    lit_110_ft = self.cam_results['lit_110'].get(name)
                    total = dark_ft + lit_80_ft + lit_110_ft

                    if total == 3:
                        self.append_result(f"{name}_cam", 1, 'PASS')
                    else:
                        self.append_result(f"{name}_cam", 0, 'FAIL', 'CAM_FAIL')
                        QTimer.singleShot(10, self.auto_fail)
                        return
                try:
                    self.stacker.done_measure.disconnect(self.ft_measure_end)
                except TypeError:
                    pass
                # if self.device_model == 'S3':
                #     QTimer.singleShot(1, self.s3_calibration)
                # else:
                QTimer.singleShot(1, self.get_stream)
        except:
            pass

    def alt_meas_end(self, dict_values):
        print('----- incoming', dict_values)
        self.bringup_results = self.bringup_results | dict_values

    # Step 3.3/5.3: Receive test values
    def ft_measure_end(self, cam_name, mode, status):
        try:
            self.auto_abort_timer.start()
            t_name = f'{cam_name}_cam_{mode}'
            print(f"--- RESULTS: {cam_name} {mode}")
            if status == 1:
                self.cam_results[mode].update({cam_name: 1})
                self.append_temp(t_name, '-', 'PASS')
            else:
                self.cam_results[mode].update({cam_name: 0})
                self.append_temp(t_name, '-', 'FAIL', 'CAM_FAIL')
                QTimer.singleShot(10, self.auto_fail)
                return

            if len(self.cam_results[mode]) < len(self.cam_list):
                next_list = [i for i in self.cam_list.keys() if i not in self.cam_results[mode].keys()]
                QTimer.singleShot(1, lambda: self.start_measure(next_list[0], mode))
            else:
                QTimer.singleShot(1, self.next_step)
        except:
            pass

    # Step 3.2/5.2: Measure dark/lit frame parameters
    def start_measure(self, cam_name, mode):
        try:
            self.auto_abort_timer.start()
            id = self.cam_list[cam_name]
            content = self.http_manager.read_response(id)
            self.stacker.onTop()
            print(f"--- MEASURE: {cam_name} {mode}")
            try:
                if mode == 'dark':
                    self.stacker.dft_start(self.current_serial, cam_name, content)
                elif mode == 'lit_80':
                    self.stacker.lft_start(self.current_serial, cam_name, 80, content)
                elif mode == 'lit_110':
                    self.stacker.lft_start(self.current_serial, cam_name, 110, content)
            except:
                res = QMessageBox.critical(self.stacker, 'Error', '', QMessageBox.StandardButton.Yes)
                self.cam_results[mode].update({cam_name: 0})
                self.append_temp(f'{cam_name}_cam_{mode}', '-', 'FAIL', 'CAM_FAIL')
                QTimer.singleShot(10, self.process_values)
                return
                # QTimer.singleShot(1, self.next_step)
        except:
            pass

    # For S3 model, receive left and right images into buffer
    def buffer(self, cam_name, id):
        self.image_resp[cam_name] = self.http_manager.read_response(id)
        # print('added to buffer', self.image_resp)

    # Step 3.1/5.1: Trigger dark/lit frame capture
    def read_image_id(self):
        try:
            self.auto_abort_timer.start()
            content = self.http_manager.read_response(self.current_mode + '_frame')
            print(self.current_mode, 'image ids:', content)

            self.stacker.wifi_widget.maintain_conn()

            if content:
                self.cam_list = json.loads(str(content, 'utf-8')) # = {'center': id, 'left': id, 'right': id}

                self.image_resp = {}
                for cam_name, id in self.cam_list.items():
                    print(f"--- REQUEST: {cam_name} {self.current_mode}")
                    if cam_name == 'center':
                        self.http_manager.get(id, 
                                            f'http://{self.device_ip}/photo/{id}', 
                                            lambda: self.start_measure(cam_name, self.current_mode))
                    else:
                        self.http_manager.get(id, 
                                            f'http://{self.device_ip}/photo/{id}', 
                                            lambda:self.buffer(cam_name, id))
            else:
                res = QMessageBox.critical(self, 'Error', 'Device API failed', QMessageBox.StandardButton.Yes)
                self.fail_reasons.append('API_FAIL')
                QTimer.singleShot(10, self.process_values)
                return
        except:
            pass

    # Step 2 loopback/end
    def off_led_status(self):
        try:
            self.auto_abort_timer.start()
            id = self.ui_colors[self.curr_color]
            response = self.http_manager.read_response(id)
            
            self.curr_color += 1
            if self.curr_color < len(self.ui_colors):
                QTimer.singleShot(1, self.get_led_status)
            else:
                print(self.ui_results)
                response = self.http_manager.read_response('default')
                self.stacker.wifi_widget.maintain_conn()
                self.current_mode = 'dark'
                self.cam_results = {'dark': {}, 'lit_80': {}, 'lit_110': {}}
                self.http_manager.post('dark_frame', 
                                    f'http://{self.device_ip}/take-pictures', 
                                    self.read_image_id,
                                    1000)
        except:
            pass

    def off_request(self):
        try:
            id = self.ui_colors[self.curr_color]
            ui_led_ctrl = {"color": id, "on": False}
            self.http_manager.put(id, 
                                f'http://{self.device_ip}/status-led', 
                                'application/json', 
                                json.dumps(ui_led_ctrl), 
                                self.off_led_status,
                                100)
        except:
            pass

    # Step 2 loop: turn off LED
    def read_led_status(self):
        try:
            self.auto_abort_timer.start()
            id = self.ui_colors[self.curr_color]
            response = self.http_manager.read_response(id)

            ind_status, rgb_values = self.stacker.add_ind_led(id)
            print(id, ind_status, self.led_retry)
            if ind_status:
                self.ui_results[id] = ind_status
                self.bringup_results[f"ui_{id}"] = rgb_values
                self.append_result(f'ui_{id}', rgb_values, 'PASS')
                # res = QMessageBox.information(self.stacker, 'Info', f'UI Led: {id}', QMessageBox.StandardButton.Yes)
                QTimer.singleShot(1000, self.off_request)
            elif self.led_retry:
                self.led_retry -= 1
                ui_led_ctrl = {"color": self.ui_colors[self.curr_color], "on": True}
                self.http_manager.put(self.ui_colors[self.curr_color], 
                                    f'http://{self.device_ip}/status-led', 
                                    'application/json', 
                                    json.dumps(ui_led_ctrl), 
                                    self.read_led_status,
                                    100)
            else:
                self.ui_results[id] = ind_status
                self.bringup_results[f"ui_{id}"] = rgb_values
                self.append_result(f'ui_{id}', rgb_values, 'FAIL', 'LED_IND_FAIL')
                QTimer.singleShot(10, self.auto_fail)
                return
        except:
            pass

    # Step 2 loop: turn on LED
    def get_led_status(self):
        try:
            self.auto_abort_timer.start()
            self.led_retry = 2
            ui_led_ctrl = {"color": self.ui_colors[self.curr_color], "on": True}
            self.http_manager.put(self.ui_colors[self.curr_color], 
                                f'http://{self.device_ip}/status-led', 
                                'application/json', 
                                json.dumps(ui_led_ctrl), 
                                self.read_led_status,
                                100)
        except:
            pass

    # Step 2: Indicator LED Test
    def indicator_led_test(self):
        try:
            self.auto_abort_timer.start()
            self.ui_colors = ['red', 'green', 'blue', 'orange']
            self.curr_color = 0
            self.ui_results = {}

            # turn off any initial indicator LEDs
            for col in self.ui_colors:
                ui_led_ctrl = {"color": col, "on": False}
                self.http_manager.put('default', 
                                    f'http://{self.device_ip}/status-led', 
                                    'application/json', 
                                    json.dumps(ui_led_ctrl))

            # Some delay to wait for LEDs to turn off
            QTimer.singleShot(1500, self.get_led_status)
        except:
            pass

    # Step 1 end
    def read_device_status(self):
        try:
            # self.auto_abort_timer.start()
            self.status_retries -= 1
            content = self.http_manager.read_response('status')
            if content:
                connection_status = json.loads(str(content, 'utf-8'))
                self.stacker.show_status(connection_status)

                fw_ver = connection_status.get('firmware_version')
                # check version if valid
                self.append_result('fw_version', fw_ver, 'PASS')
                self.bringup_results['fw_version'] = fw_ver

                batt_pct = connection_status.get('battery_level')
                self.stacker.charging_widget.set_old_level(batt_pct)
        
                self.old_batt_lvl = connection_status.get('battery_voltage_mv')
                self.append_result('battery_voltage', self.old_batt_lvl, 'PASS')
                self.bringup_results['batt_mv'] = self.old_batt_lvl

                QTimer.singleShot(1, self.indicator_led_test)
            elif self.status_retries > 0:
                self.http_manager.get('status', 
                                    f'http://{self.device_ip}/status', 
                                    self.read_device_status,
                                    1800)
            else:
                self.append_result('fw_version', '-', 'FAIL', 'DIAG_FW')
                self.append_result('battery_voltage_mv', '-', 'FAIL', 'DIAG_BATTERY')
                self.bringup_results['fw_version'] = None
                self.bringup_results['batt_mv'] = None
                # self.stacker.charging_widget.set_old_level(10)
                QTimer.singleShot(10, self.auto_fail)
                return
        except:
            pass

    # Step 1: Pre-flight Diagnostics
    def get_device_status(self, conn_status):
        try:
            # self.auto_abort_timer.start()
            try:
                self.stacker.bt_widget.progress_status.disconnect(self.messages.setText)
                self.stacker.wifi_widget.conn_success.disconnect(self.get_device_status)
            except TypeError:
                pass

            if not conn_status:
                self.append_result('ip_address', '-', 'FAIL', 'WIFI_FAIL')
                print('error connecting to AP')
                QTimer.singleShot(10, self.auto_fail)
                return
            
            self.stacker.onTop()
            self.device_ip = self.stacker.bt_widget.final_ip_addr
            self.unpair_cmd = ['daq/BluetoothDevicePairing.exe', 'unpair-by-mac', '--mac', self.mac_address, '--type', 'BluetoothLE']
            resp_unpair = subprocess.run(self.unpair_cmd)

            if not self.device_ip:
                self.append_result('ip_address', '-', 'FAIL', 'WIFI_FAIL')
                # resp = QMessageBox.warning(self, 'Error', 'No IP Address', QMessageBox.StandardButton.Ok)
                QTimer.singleShot(10, self.auto_fail)
                return
            else:
                self.bringup_results['ip_address'] = self.device_ip
                self.append_result('ip_address', self.device_ip, 'PASS')

            # Check DUT status and diagnostic details
            self.status_retries = 3
            self.http_manager.get('status', 
                                f'http://{self.device_ip}/status', 
                                self.read_device_status,
                                1500)
        except:
            pass

    # Step 0: DUT Scanning & Pairing
    def get_device_mac(self):
        try:
            # self.auto_abort_timer.start()
            # self.mac_address = 'CC:C8:37:2F:8B:29'
            
            flag, mac_error = self.dbconn.get_ble_mac(self.current_serial)
            if flag != 1:
                if self.mode_label.text() == 'PRODUCTION':
                    self.append_result('bt_mac', '-', 'FAIL', 'NO_MAC')
                    # res = QMessageBox.critical(self, 'Error', mac_error, QMessageBox.StandardButton.Yes)
                    QTimer.singleShot(10, self.auto_fail)
                    return
                else:
                    self.mac_address = 'CC:C8:37:2F:8B:29'
            else:
                self.append_result('bt_mac', mac_error, 'PASS')
                self.mac_address = mac_error

            self.bringup_results['bt_mac'] = self.mac_address
            
            self.stacker.onTop()
            self.stacker.bt_widget.progress_status.connect(self.messages.setText)
            self.stacker.wifi_widget.conn_success.connect(self.get_device_status)
            self.stacker.start_bt_scan(self.mac_address)
        except:
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
            # resp_off = subprocess.run(self.relay_off_cmd)
            self.disable_supply()

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
            # QTimer.singleShot(1, self.charging_test)
        except:
            pass

    def validate_serial(self):
        try:
            if self.login_button.text() == 'Login':
                QMessageBox.information(self, 'Info', 'Please login to start testing', buttons=QMessageBox.StandardButton.Ok)
                self.oprtr_en_input.setFocus()
                return False

            self.init_test_variables()
            self.current_serial = self.serialno_display.text().strip().upper()
            if not self.current_serial:
                QMessageBox.information(self, 'Info', 'Enter serial number to test', buttons=QMessageBox.StandardButton.Ok)
                self.serialno_display.setFocus()
                return False

            # regex = re.fullmatch(r"K/d{11}", self.current_serial)
            # if self.mode_label.text() == 'PRODUCTION' and not regex:
            #     QMessageBox.information(self, 'Info', 'Incorrect serial number format', buttons=QMessageBox.StandardButton.Ok)
            #     return

            # Dev Mode
            if self.mode_label.text() == 'DEVELOPMENT':
                self.tail_details['test_rep'] = 1
                po_flag, po_error = self.dbconn.prev_station_ok(self.current_serial)
                if not po_flag:
                    QMessageBox.warning(self, 'Error', po_error, buttons=QMessageBox.StandardButton.Ok)
                else:
                    self.ponum_display.setText(po_error)

            # Prod Mode
            else:
                self.golden_tested_flag, self.golden_sn = self.dbconn.get_golden_logs(self.config['tester_code'])
                # print(self.golden_tested_flag, self.golden_sn)
                if self.current_serial != self.golden_sn:
                    if not self.golden_tested_flag:
                        QMessageBox.critical(self, 'Error', 'Please test the Golden Sample first', QMessageBox.StandardButton.Ok)
                        return False

                    po_flag, po_error = self.dbconn.prev_station_ok(self.current_serial)
                    if not po_flag:
                        QMessageBox.warning(self, 'Error', po_error, buttons=QMessageBox.StandardButton.Ok)
                        return False
                    else:
                        self.ponum_display.setText(po_error)
            
                    flag, test_rep = self.dbconn.check_retest(self.current_serial)
                    if not flag:
                        QMessageBox.critical(self, 'Error', str(test_rep), buttons=QMessageBox.StandardButton.Ok)
                        return False
                    
                    if test_rep >= 0 and test_rep < 3:
                        self.tail_details['test_rep'] = test_rep + 1
                    elif test_rep == -1:
                        QMessageBox.warning(self, 'Warning', 'Unit already passed', buttons=QMessageBox.StandardButton.Ok)
                        return False
                    elif test_rep > 2:
                        QMessageBox.warning(self, 'Warning', 'No more retests allowed', buttons=QMessageBox.StandardButton.Ok)
                        return False

            self.start_test_button.setEnabled(True)
            return True
        except:
            pass

    def load_s3_calib(self):
        self.s3_calib_poses = {}
        calib_template = self.config.get('template_path', 'calibration_poses.txt')
        with open(calib_template, newline='') as calib_file:
            csvreader = csv.reader(calib_file, delimiter=',', quotechar='"')
            for row in csvreader:
                try:
                    self.s3_calib_poses[row[0]] = (float(row[1]), float(row[2]), float(row[3]))
                except ValueError:
                    continue
        
        # params_file = self.config['limits_path']
        # with open(params_file, 'r') as limits_file:
        #     self.test_limits = json.load(limits_file)

    def load_limits(self):
        filename = self.config['limits_path'] or 'limits.json'
        with open(filename, 'r') as limits_file:
            self.test_limits = json.load(limits_file)

        # ccc = float(self.test_limits['led_luminance']['minimum'])
        # print(ccc)

    def load_config(self):
        parser = configparser.ConfigParser()
        parser.read('Settings.ini')

        self.config = {}
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
            self.config['cam_source'] = cam_src
            self.config['cam_params'] = {'exposure': cam_exp, 'focus': cam_focus, 'zoom': cam_zoom}
        else:
            self.config['cam_source'] = 0
            self.config['cam_params'] = None

        if self.device_model == 'S3':
            try:
                from goniometer import GonioPiezo
                self.goniometer = GonioPiezo(self.config['gnm2_serial'], self.config['gnm3_serial'], self.config['pdxc2_serial'])
                self.load_s3_calib()
            except:
                self.messages.setText('GNM2, GNM3, PDXC2 not detected')

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
            # final = self.head_details | self.all_results | self.tail_details
            final = self.head_details | self.bringup_results | self.tail_details
            writer.writerow(final)

    def closeEvent(self, event):
    #     if not self.isActiveWindow():
    #         self.activateWindow()
    #     result = QMessageBox.warning(self, 'Warning', 'Are you sure you want to exit?', 
    #                         buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

    #     if result == QMessageBox.StandardButton.Yes:
        try:
            self.goniometer.stop_disconnect()
        except:
            pass
        self.stacker.destroy()
        event.accept()
    #     else:
    #         event.ignore()

if __name__=='__main__':
    # CLI_PREFIX = os.getenv('CLI_PREFIX', 'C:\\Users\\ai\\')
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')
    window = MainWindow()
    window.show()

    # Start the event loop.
    sys.exit(app.exec())