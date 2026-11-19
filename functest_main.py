import os
import re
import sys
import csv
import time
import json
import serial
import subprocess
import configparser
from datetime import datetime, time as dtime

from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QInputDialog, QLineEdit
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence, QStandardItemModel, QStandardItem
from PyQt6.QtCore import QTimer, Qt

from database import DatabaseConn, GoldenSample
from helper_classes import HTTPRequest, SettingsBox, StackedUI
from goniometer import Goniometer

# import logging
# logger = logging.getLogger(__name__)

Q_OK = QMessageBox.StandardButton.Ok
Q_YES = QMessageBox.StandardButton.Yes
Q_NO = QMessageBox.StandardButton.No

def get_path(raw_path):
    try:
        ret = os.path.join(sys._MEIPASS, raw_path)
    except AttributeError:
        ret = os.path.join('.', raw_path)
    return ret

from cryptography.fernet import Fernet
def decrypt(enc_message: str):
    enc_bytes = enc_message.encode()
    secret_key = b'wCzGXclj_DVpjx-2WzvTZI2i8chvybK82t-2eFkO8Ag='
    fernet = Fernet(secret_key)
    dec_bytes = fernet.decrypt(enc_bytes)
    return dec_bytes.decode()

class MainWindow(QMainWindow):
    def __init__(self, version):
        super().__init__()
        uic.loadUi(get_path('layout/functional_test.ui'), self)
        kaertech_logo = QImage(get_path(r"static/kaertech_logo.png")).scaled(90, 90, transformMode=Qt.TransformationMode.SmoothTransformation)
        self.logo_left.setPixmap(QPixmap.fromImage(kaertech_logo))
        dm_logo = QImage(get_path(r"static/dental_logo.png")).scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
        self.logo_right.setPixmap(QPixmap.fromImage(dm_logo))
        self.version.setText(version)

        self.config = {}
        self.goniometer = None
        self.s3_calib_poses = {}
        self.csv_headers = ['serial_num', 'po_num', 'operator_en', 'shift', 'date_time', 'tester_code',
                            'bt_mac', 'ip_address', 'fw_version', 'batt_mv', 
                            'ui_red', 'ui_green', 'ui_blue', 'ui_orange', 
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

        self.auto_abort_timer = QTimer()
        self.auto_abort_timer.setInterval(1000 * 45)
        self.auto_abort_timer.timeout.connect(lambda: self.add_fail_reason('TEST TIMEOUT'))
        
        if self.device_model == 'S3':
            self.bar_step = 100 / 53
        else:
            self.bar_step = 100 / 25
        self.start_test_button.setEnabled(False)

        self.http_manager = HTTPRequest(self)
        self.stacker = StackedUI(self.test_limits, self.config)
        # self.stacker.fail_reason.connect(self.add_fail_reason)
        self.stacker.alt_signal.connect(self.alt_meas_end)
        self.stacker.led_okay.connect(self.led_catcher)

        self.hw_timer = QTimer(self)
        self.hw_timer.timeout.connect(self.update_current)
        self.connect_port()

        self.wifi_timer = QTimer(self)
        self.wifi_timer.timeout.connect(self.stacker.wifi_widget.maintain_conn)
        self.wifi_timer.setInterval(5000)

        # Connect button click signal to slot
        self.start_test_button.clicked.connect(self.start_test)
        self.serialno_display.returnPressed.connect(self.validate_serial)
        self.serialno_display.textChanged.connect(self.reinit)
        self.settings_button.clicked.connect(self.open_settings)
        self.abort_button.clicked.connect(self.abort_signal)

    def reinit(self, new_text):
        if new_text == '':
            self.init_test_variables()
            self.start_test_button.setEnabled(False)
            self.abort_button.setEnabled(False)

    def keyPressEvent(self, a0):
        if a0.isInputEvent() and a0.text() != '':
            if not self.serialno_display.hasFocus():
                self.serialno_display.keyPressEvent(a0)
        return super().keyPressEvent(a0)
    
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
            self.init_test_variables()

    def connect_port(self):
        try:
            self.hw_serial = serial.Serial(port = self.config['hw_port'], baudrate = int(self.config['baud_rate']))
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.messages.setText(f'POWER SUPPLY AT {self.config["hw_port"]}')
            self.enable_supply(False)
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

    def enable_supply(self, state: bool):
        if not self.hw_serial:
            return None
        try:
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            if state:
                self.hw_serial.write(b'OUT1\r\n')
            else:
                self.hw_serial.write(b'OUT0\r\n')
            self.hw_serial.flush()
            # response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
        except:
            pass

    def abort_signal(self):
        self.auto_abort_timer.stop()
        self.wifi_timer.stop()
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
        self.settings_button.setEnabled(state)

    def init_test_variables(self):
        # try:
        #     self.goniometer.set_home()
        # except:
        #     pass
        
        self.current_serial = ''
        self.mac_address = ''
        self.head_details = {'serial_num': '', 'po_num': '', 'operator_en': '', 'shift': '', 'date_time': '', 'tester_code': ''}
        self.tail_details = {'duration': '', 'test_rep': '', 'status': ''}

        self.led_results = {}
        self.cam_results = {}
        # self.stream_results = {}
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
        self.tableModel.setHorizontalHeaderLabels(['Test', 'Low', 'Value', 'High', 'Status', 'Fail Reason'])
        self.tableView.setColumnWidth(0, 140)
        self.tableView.setColumnWidth(1, 120)
        self.tableView.setColumnWidth(2, 120)
        self.tableView.setColumnWidth(3, 120)
        self.tableView.setColumnWidth(4, 73)
        self.tableView.setColumnWidth(5, 170)

    # Add entry to table and final values
    def append_result(self, param_name: str, value, status: str, fail_reason = '', low: str = '', high: str = ''):
        test_item = QStandardItem(param_name.upper())
        low_item = QStandardItem(low)
        value_item = QStandardItem(str(value))
        high_item = QStandardItem(high)
        status_item = QStandardItem(status)
        fail_item = QStandardItem(fail_reason)
        self.tableModel.appendRow([test_item, low_item, value_item, high_item, status_item, fail_item])

        if status == 'PASS':
            self.all_results[param_name] = 1
        else:
            self.all_results[param_name] = 0
            self.fail_reasons.append(fail_reason)

        self.progress_percent += self.bar_step
        self.test_progress.setValue(round(self.progress_percent))

    # Add entry to table only (not in final values)
    def append_temp(self, param_name: str, value, status: str, fail_reason = '', low: str = '', high: str = ''):
        test_item = QStandardItem(param_name)
        low_item = QStandardItem(low)
        value_item = QStandardItem(str(value))
        high_item = QStandardItem(high)
        status_item = QStandardItem(status)
        fail_item = QStandardItem(fail_reason)
        self.tableModel.appendRow([test_item, low_item, value_item, high_item, status_item, fail_item])

    def add_fail_reason(self, fr_text: str):
        self.fail_reasons.append(fr_text)
        QTimer.singleShot(10, self.auto_fail)
        self.activateWindow()
        resp = QMessageBox.critical(self, 'Device FAIL', f"Reason code: {fr_text}", Q_OK)

    def test_end(self):
        self.messages.setText('Enter serial number to start testing')
        self.enable_buttons(True)
        self.serialno_display.setText('')
        self.serialno_display.setFocus()
        self.serialno_display.textChanged.connect(self.reinit)

        # now = datetime.now().time()
        # end_A = dtime(19,0,0)
        # end_B = dtime(7,0,0)
        # if self.golden_tested_flag == True:
        #     if now >= end_A or now >= end_B:
        #         self.golden_tested_flag = False

    def upload_results(self):
        self.messages.setText('Uploading results...')
        self.write_csv()
        if self.mode_label.text() == 'PRODUCTION':
            try:
                if self.current_serial == self.golden_sample.serial():
                    self.golden_sample.log_test(self.tail_details['status'])
                else:
                    success, error_code = self.dbconn.db_insert(self.head_details, self.bringup_results, self.tail_details)
                    if not success:
                        QMessageBox.critical(self, 'Error', error_code, buttons=Q_OK)
                # ret = self.dbconn.get_db_status(self.current_serial)
            except Exception as e:
                # print('upload failed')
                QMessageBox.warning(self, 'Error', f'Database upload failed.\n{e}', Q_OK)

        QTimer.singleShot(1, self.test_end)

    def auto_fail(self):
        try:
            print('--- auto fail start')
            self.auto_abort_timer.stop()
            self.activateWindow()
            self.test_progress.setValue(100)

            self.unpair_cmd = ['daq/BluetoothDevicePairing.exe', 'unpair-by-mac', '--mac', self.mac_address, '--type', 'BluetoothLE']
            resp_unpair = subprocess.run(self.unpair_cmd)

            self.wifi_timer.stop()
            self.stacker.wifi_widget.forget_network()
            self.enable_supply(False)

            if self.config['use_lan'] or self.stacker.wifi_widget.restored:
                QTimer.singleShot(10, self.auto_fail_end)
            else:
                try:
                    self.stacker.wifi_widget.net_restored.disconnect()
                except TypeError:
                    pass
                self.stacker.wifi_widget.net_restored.connect(self.auto_fail_end)
        except Exception as e:
            QMessageBox.warning(self, 'Error', str(e), Q_OK)

    def auto_fail_end(self):
        try:
            print('--- auto fail end')
            try:
                self.stacker.wifi_widget.net_restored.disconnect(self.auto_fail_end)
            except TypeError:
                pass

            self.stacker.abort_test()

            test_duration = time.time() - self.start_test_time
            self.duration_display.setText(f"{test_duration:.4f}")
            self.tail_details['duration'] = f"{test_duration:.2f}"
            # self.messages.setText('Test timeout.')

            self.tail_details['status'] = 0
            self.tail_details['fail_reason'] = ','.join(self.fail_reasons)[:1000]
            self.pass_fail_output.setText("FAIL")
            self.pass_fail_output.setStyleSheet("background-color: rgb(245, 0, 0);")

            self.stacker.wifi_widget.maintain_conn(True)
            QTimer.singleShot(500, self.upload_results)
        except Exception as e:
            QMessageBox.warning(self, 'Error', str(e), Q_OK)

    # End of functional test process
    def process_values(self):
        try:
            self.auto_abort_timer.stop()
            self.activateWindow()
            self.test_progress.setValue(100)
            try:
                self.stacker.wifi_widget.net_restored.disconnect(self.process_values)
            except TypeError:
                pass

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

            self.stacker.wifi_widget.maintain_conn(True)
            QTimer.singleShot(500, self.upload_results)
        except Exception as e:
            QMessageBox.warning(self, 'Processing', str(e), Q_OK)

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
            self.enable_supply(False)
            # self.stacker.wifi_widget.forget_network()

            if self.config['use_lan'] or self.stacker.wifi_widget.restored:
                QTimer.singleShot(1, self.process_values)
            else:
                self.stacker.wifi_widget.net_restored.connect(self.process_values)
        except Exception as e:
            QMessageBox.warning(self, 'Charging End', str(e), Q_OK)

    # Step 8. Charging Test
    def charging_test(self):
        try:
            self.wifi_timer.stop()
            self.messages.setText('Charging device...')
            self.auto_abort_timer.start()
            print('Step 8 start')
            self.enable_supply(True)
            try:
                self.stacker.done_stream.disconnect(self.close_stream)
            except TypeError:
                pass
            self.stacker.s7_chrg.setChecked(True)

            if self.hw_serial:
                self.hw_timer.start(1000)

            self.stacker.wifi_widget.forget_network()
            QTimer.singleShot(5000, self.charging_status)
        except Exception as e:
            QMessageBox.warning(self, 'Charging Start', str(e), Q_OK)

    def led_405_off(self):
        try:
            self.auto_abort_timer.start()
            print('--- UV Light OFF')
            content = self.http_manager.read_response('uv')

            res = QMessageBox.question(self.stacker, 'Info', 'Are the UV LEDs turned on?', buttons=Q_YES | Q_NO)
            if res == Q_YES:
                self.append_result('led_uv', '-', 'PASS')
                self.http_manager.put('uv', 
                                        f'http://{self.device_ip}/torch-mode', 
                                        'text/plain', 
                                        'off',
                                        self.charging_test,
                                        100)
                # QTimer.singleShot(10, self.s3_calibration)
            else:
                self.append_result('led_uv', '-', 'FAIL', 'LED_FAIL')
                QTimer.singleShot(10, self.auto_fail)
                return
            
        except Exception as e:
            QMessageBox.warning(self, '405 Off', str(e), Q_OK)

    def led_405_on(self):
        try:
            self.auto_abort_timer.start()
            print('--- UV Light ON')
            self.messages.setText('Turning on UV Light...')
            self.http_manager.put('uv', 
                                    f'http://{self.device_ip}/torch-mode', 
                                    'text/plain', 
                                    'fluorescent')
            QTimer.singleShot(1000, self.led_405_off)
        except Exception as e:
            QMessageBox.warning(self, '405 On', str(e), Q_OK)

    # Step 7 end
    def close_stream(self, curr_cam):
        try:
            self.auto_abort_timer.start()

            if 'STREAM_FAIL' not in self.fail_reasons:
                self.bringup_results['streaming'] = 1
                self.append_result('stream', '-', 'PASS')
                
            # self.stacker.wifi_widget.maintain_conn()
            # self.http_manager.put('white', 
            #                     f'http://{self.device_ip}/torch-mode', 
            #                     'text/plain', 
            #                     'off', 
            #                     self.charging_test,
            #                     100)
            QTimer.singleShot(10, self.led_405_on)
        except Exception as e:
            QMessageBox.warning(self, 'Close Stream', str(e), Q_OK)

    # Step 7. Streaming & Connectivity Test
    def get_stream(self):
        self.messages.setText('Starting stream...')
        self.auto_abort_timer.start()

        try:
            stream_link = f'http://{self.device_ip}/preview-stream?camera=center'
            self.stacker.onTop()
            self.stacker.stream_start(stream_link)

        except Exception as e:
            resp = QMessageBox.critical(self, 'Start Stream', str(e), Q_YES)
            self.append_result('stream', '-', 'FAIL', 'STREAM_FAIL')
            QTimer.singleShot(10, self.auto_fail)
            return

    # Step 6.5 Validate
    def s3_validate(self):
        # self.pose_index starts at 30 here, until 39
        pass

    # next step
    def s3_looper(self):
        if self.cradle_position == 80:
            self.pose_index = 0
            self.cradle_position = 110
            self.goniometer.setHome()
            res = QMessageBox.information(self, 'Info', 'Lift the cradle to 110mm position', Q_OK)
            QTimer.singleShot(1, self.get_s3_img)
            return
        else:
            QTimer.singleShot(10, self.s3_validate)

    # Step 6.4: Calibrate
    def retrieve_s3_img(self):
        roll, pitch, yaw = self.s3_calib_poses.get(self.pose_index)
        if not self.calib_images[self.pose_index][f'left_{self.cradle_position}']:
            left_resp = self.http_manager.read_response('left')
            filename = self.stacker.save_image(self.current_serial, f"L{self.pose_index}", left_resp)
            self.calib_images[self.pose_index][f'left_{self.cradle_position}'] = filename
            return
        
        if not self.calib_images[self.pose_index][f'right_{self.cradle_position}']:
            right_resp = self.http_manager.read_response('right')
            filename = self.stacker.save_image(self.current_serial, f"R{self.pose_index}", right_resp)
            self.calib_images[self.pose_index][f'right_{self.cradle_position}'] = filename
            return

        if self.calib_images[self.pose_index][f'left_{self.cradle_position}'] and self.calib_images[self.pose_index][f'right_{self.cradle_position}']:
            roll, pitch, yaw = self.s3_calib_poses.get(self.pose_index)
            self.stacker.show_positions(roll, pitch, yaw)
            self.pose_index += 1
            QTimer.singleShot(1, self.get_s3_img)

    # Step 6.3: Get Image IDs
    def read_s3_img(self):
        req_id = 'calib_' + self.pose_index
        image_ids = self.http_manager.read_response(req_id)
        left_id = image_ids['left']
        right_id = image_ids['right']

        # self.calib_images[self.pose_index] = {'left_80': None, 'right_80': None, 'left_110': None, 'right_110': None}
        self.http_manager.get('left', f'http://{self.device_ip}/photos/{left_id}', self.retrieve_s3_img)
        self.http_manager.get('right', f'http://{self.device_ip}/photos/{right_id}', self.retrieve_s3_img)

    # Step 6.2: Trigger capture
    def get_s3_img(self):
        if self.pose_index == 30: # 30 pairs for calibration, 10 pairs for validation
            QTimer.singleShot(1, self.s3_looper)
            return

        roll, pitch, yaw = self.s3_calib_poses.get(self.pose_index)
        self.goniometer.setPos(roll, pitch, yaw)

        req_id = 'calib_' + self.pose_index
        self.http_manager.post(req_id, f'http://{self.device_ip}/take-pictures', self.read_s3_img)

    # Step 6.1: S3 Calibration
    def s3_calibration(self):
        try:
            res = QMessageBox.information(self, 'Info', 'Ensure the cradle is lowered to 80mm position', Q_OK)
            self.pose_index = 0
            self.cradle_position = 80
            self.calib_images = [{'left_80': None, 'right_80': None, 'left_110': None, 'right_110': None} for i in self.s3_calib_poses]
            self.goniometer.setHome()
            QTimer.singleShot(1, self.get_s3_img)
        except Exception as e:
            res = QMessageBox.warning(self, 'S3 Calibration', str(e), Q_OK)
            QTimer.singleShot(10, self.auto_fail)
            return

    # Step 4 end
    def torch_enabled(self):
        try:
            self.auto_abort_timer.start()
            content = self.http_manager.read_response('white')
            
            res = QMessageBox.question(self.stacker, 'Info', 'Are the white LEDs turned on?', buttons=Q_YES | Q_NO)
            if res == Q_YES:
                # self.stacker.wifi_widget.maintain_conn()
                self.append_result('led_white', '-', 'PASS')
                self.lit80_retry = 6
                self.current_mode = 'lit_80'
                self.http_manager.post('lit_80_frame', 
                                    f'http://{self.device_ip}/take-pictures', 
                                    self.read_image_id,
                                    1500)
            else:
                self.append_result('led_white', '-', 'FAIL', 'LED_FAIL')
                QTimer.singleShot(10, self.auto_fail)
                return
        except Exception as e:
            QMessageBox.warning(self, 'Torch Off', str(e), Q_OK)

    # Step 4: White LED Test
    def torch_test(self):
        try:
            self.auto_abort_timer.start()
            print('--- TORCH ON')
            self.messages.setText('Turning on Torch LEDs...')
            # increased delay to allow LEDs to turn on
            # self.stacker.wifi_widget.maintain_conn()
            self.http_manager.put('white', 
                                f'http://{self.device_ip}/torch-mode', 
                                'text/plain', 
                                'white')
            # self.http_manager.put('fluorescent', f'http://{self.device_ip}/torch-mode', 'text/plain', 'fluorescent')
            QTimer.singleShot(1000, self.torch_enabled)
        except Exception as e:
            QMessageBox.warning(self, 'Torch On', str(e), Q_OK)

    def led_catcher(self, lumi_val):
        self.bringup_results['led_white'] = round(lumi_val, 3)
        self.append_temp('led_white', round(lumi_val, 3), '-')
        # change to Not Temp when limits are finalized
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
                # self.stacker.wifi_widget.maintain_conn()
                res = QMessageBox.information(self.stacker, 'IMPORTANT', 'Lift the DUT cradle to 110mm position then click OK', Q_OK)
                self.lit110_retry = 6
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
                #     QTimer.singleShot(1, self.led_405_on)
                # else:
                QTimer.singleShot(1, self.get_stream)
        except Exception as e:
            QMessageBox.warning(self, 'Error', str(e), Q_OK)

    def alt_meas_end(self, dict_values):
        print('----- incoming', dict_values)
        self.bringup_results = self.bringup_results | dict_values
        for key, values in dict_values.items():
            self.append_result(key, values, 'PASS')

    # Step 3.3/5.3: Receive test values
    def ft_measure_end(self, cam_name, mode, status):
        try:
            self.auto_abort_timer.start()
            t_name = f'{cam_name}_cam_{mode}'
            print(f"--- RESULTS: {cam_name} {mode} {status}")
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
        except Exception as e:
            QMessageBox.warning(self, f'Image Results - {self.current_mode}', str(e), Q_OK)

    # Step 3.2/5.2: Measure dark/lit frame parameters
    def start_measure(self, cam_name, mode):
        try:
            self.auto_abort_timer.start()
            id = self.cam_list[cam_name]
            content = self.http_manager.read_response(id)
            self.stacker.onTop()
            print(f"--- MEASURE: {cam_name} {mode}")
            self.messages.setText('Measuring image parameters...')
            try:
                if mode == 'dark':
                    self.stacker.dft_start(self.current_serial, cam_name, content)
                elif mode == 'lit_80':
                    self.stacker.lft_start(self.current_serial, cam_name, 80, content)
                elif mode == 'lit_110':
                    self.stacker.lft_start(self.current_serial, cam_name, 110, content)
            except Exception as e:
                res = QMessageBox.critical(self.stacker, 'Error', str(e), Q_YES)
                self.cam_results[mode].update({cam_name: 0})
                self.append_temp(f'{cam_name}_cam_{mode}', '-', 'FAIL', 'CAM_FAIL')
                QTimer.singleShot(10, self.process_values)
                return
                # QTimer.singleShot(1, self.next_step)
        except Exception as e:
            QMessageBox.warning(self, f'Measure - {self.current_mode}', str(e), Q_OK)

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
            self.messages.setText('Retrieving image...')

            # self.stacker.wifi_widget.maintain_conn()

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
            elif self.current_mode == 'dark' and self.dark_test_retry > 0:
                self.dark_test_retry -= 1
                print('dark', self.dark_test_retry)
                self.http_manager.post('dark_frame', 
                                        f'http://{self.device_ip}/take-pictures', 
                                        self.read_image_id,
                                        1000)
            elif self.current_mode == 'lit_80' and self.lit80_retry > 0:
                self.lit80_retry -= 1
                print('lit80', self.lit80_retry)
                self.current_mode = 'lit_80'
                self.http_manager.post('lit_80_frame', 
                                    f'http://{self.device_ip}/take-pictures', 
                                    self.read_image_id,
                                    1500)
            elif self.current_mode == 'lit_110' and self.lit110_retry > 0:
                self.lit110_retry -= 1
                print('lit110', self.lit110_retry)
                self.current_mode = 'lit_110'
                self.http_manager.post('lit_110_frame', 
                                            f'http://{self.device_ip}/take-pictures', 
                                            self.read_image_id,
                                            1000)
            else:
                res = QMessageBox.critical(self, 'Error', 'Device API failed', Q_YES)
                self.fail_reasons.append('API_FAIL')
                QTimer.singleShot(10, self.process_values)
                return
        except Exception as e:
            QMessageBox.warning(self, f'Read Image - {self.current_mode}', str(e), Q_OK)

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
                # self.stacker.wifi_widget.maintain_conn()
                self.dark_test_retry = 6
                self.current_mode = 'dark'
                self.cam_results = {'dark': {}, 'lit_80': {}, 'lit_110': {}}
                self.http_manager.post('dark_frame', 
                                    f'http://{self.device_ip}/take-pictures', 
                                    self.read_image_id,
                                    1000)
        except Exception as e:
            QMessageBox.warning(self, 'Indicator LED', str(e), Q_OK)

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
        except Exception as e:
            QMessageBox.warning(self, 'Indicator LED - Off', str(e), Q_OK)

    # Step 2 loop: turn off LED
    def read_led_status(self):
        try:
            self.auto_abort_timer.start()
            id = self.ui_colors[self.curr_color]
            response = self.http_manager.read_response(id)

            # ind_status, rgb_values = self.stacker.add_ind_led(id)
            ind_result = self.stacker.add_ind_led(id)
            ind_status = ind_result.get('status')
            rgb_values = ind_result.get('value')
            limits = ind_result.get('limits')
            print(id, ind_status, self.led_retry)
            if ind_status:
                self.ui_results[id] = ind_status
                self.bringup_results[f"ui_{id}"] = rgb_values
                self.append_result(f'ui_{id}', rgb_values, 'PASS', low=limits)
                # res = QMessageBox.information(self.stacker, 'Info', f'UI Led: {id}', Q_YES)
                QTimer.singleShot(1000, self.off_request)
            elif self.led_retry:
                self.__reset_leds()
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
                self.append_result(f'ui_{id}', rgb_values, 'FAIL', 'LED_IND_FAIL', low=limits)
                QTimer.singleShot(10, self.auto_fail)
                return
        except Exception as e:
            QMessageBox.warning(self, 'Indicator LED - Read', str(e), Q_OK)

    # Step 2 loop: turn on LED
    def get_led_status(self):
        try:
            self.auto_abort_timer.start()
            self.led_retry = 6
            self.messages.setText(f'Testing indicator LEDs: {self.ui_colors[self.curr_color].upper()}')
            ui_led_ctrl = {"color": self.ui_colors[self.curr_color], "on": True}
            self.http_manager.put(self.ui_colors[self.curr_color], 
                                f'http://{self.device_ip}/status-led', 
                                'application/json', 
                                json.dumps(ui_led_ctrl), 
                                self.read_led_status,
                                100)
        except Exception as e:
            QMessageBox.warning(self, 'Indicator LED - Get', str(e), Q_OK)

    def __reset_leds(self):
        for col in self.ui_colors:
            ui_led_ctrl = {"color": col, "on": False}
            self.http_manager.put('default', 
                                f'http://{self.device_ip}/status-led', 
                                'application/json', 
                                json.dumps(ui_led_ctrl))

    # Step 2: Indicator LED Test
    def indicator_led_test(self):
        try:
            self.auto_abort_timer.start()
            self.ui_colors = ['red', 'green', 'blue', 'orange']
            self.curr_color = 0
            self.ui_results = {}

            # turn off any initial indicator LEDs
            self.__reset_leds()

            # Some delay to wait for LEDs to turn off
            QTimer.singleShot(1500, self.get_led_status)
        except Exception as e:
            QMessageBox.warning(self, 'Indicator LED - Start', str(e), Q_OK)

    # Step 1 end
    def read_device_status(self):
        try:
            self.auto_abort_timer.start()
            self.status_retries -= 1
            print('--- status retry:', self.status_retries)
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
        except Exception as e:
            QMessageBox.warning(self, 'Device Status', str(e), Q_OK)

    def fail_catcher(self, fail_reason):
        if self.bt_wifi_retries > 0:
            self.auto_abort_timer.start()
            print('retrying...', self.bt_wifi_retries)
            self.stacker.bt_widget.progress_status.connect(self.messages.setText)
            self.stacker.wifi_widget.conn_success.connect(self.get_device_status)
            self.bt_wifi_retries -= 1
            self.stacker.start_bt_scan(self.mac_address)
        else:
            self.add_fail_reason(fail_reason)

    # Step 1: Pre-flight Diagnostics
    def get_device_status(self, conn_status):
        try:
            self.auto_abort_timer.start()
            try:
                self.stacker.fail_reason.disconnect(self.fail_catcher)
                self.stacker.fail_reason.connect(self.add_fail_reason)
            except TypeError:
                pass
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
                # resp = QMessageBox.warning(self, 'Error', 'No IP Address', Q_OK)
                QTimer.singleShot(10, self.auto_fail)
                return
            else:
                self.bringup_results['ip_address'] = self.device_ip
                self.append_result('ip_address', self.device_ip, 'PASS')

            self.wifi_timer.start()
            # Check DUT status and diagnostic details
            self.messages.setText('Reading diagnostic status...')
            self.status_retries = 6
            self.http_manager.get('status', 
                                f'http://{self.device_ip}/status', 
                                self.read_device_status,
                                1500)
        except Exception as e:
            QMessageBox.warning(self, 'DUT Pairing', str(e), Q_OK)

    # Step 0: DUT Scanning & Pairing
    def get_device_mac(self):
        try:
            self.auto_abort_timer.start()
            # self.mac_address = 'CC:C8:37:2F:8B:29'
            
            result = self.dbconn.get_ble_mac(self.current_serial)
            if 'error' in  result:
                if self.mode_label.text() == 'PRODUCTION':
                    self.append_result('bt_mac', '-', 'FAIL', 'NO_MAC')
                    # res = QMessageBox.critical(self, 'Error', mac_error, Q_YES)
                    QTimer.singleShot(10, self.auto_fail)
                    return
                else:
                    self.mac_address = 'CC:C8:37:2F:8B:29'
            else:
                self.mac_address = result.get('bt_mac')

            self.bringup_results['bt_mac'] = self.mac_address
            self.append_result('bt_mac', self.mac_address, 'PASS')
            
            self.stacker.onTop()
            try:
                self.stacker.fail_reason.disconnect(self.add_fail_reason)
            except TypeError:
                pass
            self.stacker.fail_reason.connect(self.fail_catcher)
            self.stacker.bt_widget.progress_status.connect(self.messages.setText)
            self.stacker.wifi_widget.conn_success.connect(self.get_device_status)
            self.bt_wifi_retries = 2
            self.stacker.start_bt_scan(self.mac_address)
        except Exception as e:
            QMessageBox.warning(self, 'Device MAC', str(e), Q_OK)

    def start_test(self):
        try:
            # self.forget_connection()
            if not self.hw_serial:
                res = QMessageBox.critical(self, 'Error', 'Power Supply not detected', Q_OK)
                if self.mode_label.text() == 'PRODUCTION':
                    return
            
            self.serialno_display.textChanged.disconnect(self.reinit)
            if self.current_serial == '':
                val = self.validate_serial()
                if not val:
                    return

            self.stacker.reset_holders()
            self.enable_supply(False)

            self.head_details['serial_num'] = self.current_serial.upper()
            self.append_temp('serial_num', self.current_serial.upper(), '-')
            self.head_details['po_num'] = self.ponum_display.text().upper()
            if len(self.config['tester_code']) > 0:
                self.head_details['tester_code'] = self.config['tester_code'].upper()
            self.head_details['operator_en'] = self.oprtr_display.text()
            self.head_details['shift'] = self.shift_display.text()
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
            
        except Exception as e:
            QMessageBox.warning(self, 'Test Start', str(e), Q_OK)

    def validate_serial(self):
        self.stacker.wifi_widget.maintain_conn(True)
        try:
            user = self.oprtr_display.text()
            shift = self.shift_display.text()
            if not (user and shift):
                # if self.mode_label.text() == 'PRODUCTION':
                QMessageBox.information(self, 'Info', 'Please login to start testing', buttons=Q_OK)
                QTimer.singleShot(10, self.open_settings)
                self.serialno_display.setText('')
                return False

            self.init_test_variables()
            self.current_serial = self.serialno_display.text().upper()
            if self.current_serial == '':
                QMessageBox.information(self, 'Info', 'Enter serial number to test', buttons=Q_OK)
                self.serialno_display.setFocus()
                return False
            
            if self.mode_label.text() == 'PRODUCTION' and not self.golden_sample.logged():
                # GS has not been tested for the shift
                if self.current_serial != self.golden_sample.serial():
                    # Entered serial is not GS
                    QMessageBox.critical(self, 'Error', 'Please test the Golden Sample first', Q_OK)
                    return False
                else:
                    # Entered serial is GS
                    self.start_test_button.setEnabled(True)
                    return True

            db_status = self.dbconn.get_db_status(self.current_serial)
            if 'Database connection error' in db_status.get('error', ''):
                self.stacker.wifi_widget.maintain_conn(True)
                QTimer.singleShot(2000, self.validate_serial)
                return

            if 'error' in db_status and self.mode_label.text() == 'PRODUCTION':
                QMessageBox.warning(self, 'Error', db_status['error'], buttons=Q_OK)
                # if self.mode_label.text() == 'PRODUCTION':
                return False

            if db_status.get('need_pass', False):
                text, ok = QInputDialog.getText(self, 'Retest authorization', 'Password:', QLineEdit.EchoMode.Password)
                if ok and text == decrypt(self.config['fa_password']):
                    pass
                else:
                    return False

            po_num = db_status.get('po_num', '')
            self.ponum_display.setText(po_num)
            test_rep = db_status.get('test_rep', 1)

            self.tail_details['test_rep'] = test_rep
            self.start_test_button.setEnabled(True)
            return True
        except Exception as e:
            QMessageBox.warning(self, 'Validate', str(e), Q_OK)

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

        print(self.s3_calib_poses)
        
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
            db_config = {}
            for key, raw_value in parser['Database'].items():
                key_std = key.lower().replace(' ', '_')
                db_config[key_std] = raw_value.strip('"')

            db_model = db_config.get('model', '').upper()
            self.config['database'] = db_config
            self.config['use_lan'] = parser.getboolean('Database', 'LAN')
            self.dbconn = DatabaseConn(self.config['database'])
            # self.golden_sample = GoldenSample(db_model, self.config.get('tester_code', ''))
            self.golden_sample = GoldenSample('Soniquemini', 'LSMPPRGT-01-0004648')
            if 'S3' in db_model and 'M1' not in db_model:
                self.device_model = 'S3'
            else:
                self.device_model = 'M1'
            self.model_display.setText(db_model)
        else:
            self.config['database'] = {}
            self.dbconn = None
            self.golden_sample = None

        if 'Camera' in parser.sections():
            cam_params = {}
            for key, raw_value in parser['Camera'].items():
                key_std = key.lower().replace(' ', '_')
                cam_params[key_std] = int(raw_value.strip('"'))

            self.config['cam_params'] = cam_params
        else:
            self.config['cam_params'] = {}
        
        if self.device_model == 'S3' and 'LumyoS3' in parser.sections():
            pdxc2_serial = parser.get('LumyoS3', 'PDXC2 Serial')
            gnm2_serial = parser.get('LumyoS3', 'GNM2 Serial')
            gnm3_serial = parser.get('LumyoS3', 'GNM3 Serial')

            self.config['3-axis'] = {'pdxc2': pdxc2_serial, 'gnm2': gnm2_serial, 'gnm3': gnm3_serial}
            self.goniometer = Goniometer(self.config['3-axis'])
            self.load_s3_calib()
        else:
            self.config['3-axis'] = {}
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
        response = QMessageBox.question(self, 'Exit app', "Confirm exit?", Q_YES|Q_NO)
        if response == Q_YES:
            try:
                self.goniometer.disconnect()
            except:
                pass
            self.stacker.destroy()
            event.accept()
        else:
            event.ignore()

if __name__=='__main__':
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')
    window = MainWindow('v1.2.2')
    window.show()

    # Start the event loop.
    sys.exit(app.exec())