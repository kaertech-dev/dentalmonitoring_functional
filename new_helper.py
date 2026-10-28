import os
import sys
import cv2
import json
import pywifi
import numpy as np
import subprocess
from datetime import datetime
from pywifi import const

import PyQt6.QtBluetooth as QtBt
from PyQt6.QtWidgets import QApplication, QDialog, QGraphicsScene, QWidget, QButtonGroup, QGraphicsRectItem, QGraphicsLineItem
from PyQt6.QtGui import QPen, QColor, QPixmap, QImage, QBrush, QStandardItemModel, QStandardItem
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl, QObject, QByteArray
from PyQt6 import uic

def get_path(raw_path):
    try:
        ret = os.path.join(sys._MEIPASS, raw_path)
    except Exception:
        ret = os.path.join('.', raw_path)
    return ret

class BtWifiWorker(QObject):
    finished = pyqtSignal(bool)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)
    found_ip = pyqtSignal(str)

    def __init__(self, mac_address, ap_ssid, ap_password, parent=None, bt_timeout=30, ap_timeout=30):
        super().__init__(parent)

        # ===== Bluetooth Config ===== #
        self.mac_object = QtBt.QBluetoothAddress(mac_address)
        self.final_ip_addr = ''
        self.dut_ssid = ap_ssid
        self.dut_pass = ap_password
        self.bt_timeout = bt_timeout

        # ===== WiFi Config ===== #
        wifi = pywifi.PyWiFi()
        self.iface = wifi.interfaces()[0]

        self.profile = pywifi.Profile()
        self.profile.ssid = ap_ssid
        self.profile.auth = const.AUTH_ALG_OPEN
        self.profile.akm.append(const.AKM_TYPE_WPA2PSK)
        self.profile.cipher = const.CIPHER_TYPE_CCMP
        self.profile.key = ap_password

        check_interval = 500
        self.conn_t = QTimer(parent=self)
        self.conn_t.setInterval(check_interval)
        self.conn_t.timeout.connect(self.check_connected)
        self.max_timeout = (1000/check_interval) * ap_timeout

    def bt_scan(self):
        self.scanner = QtBt.QBluetoothDeviceDiscoveryAgent(self)
        self.scanner.setLowEnergyDiscoveryTimeout(1000 * self.bt_timeout)
        self.progress.emit('SCANNING FOR DEVICE...')
        self.scanner.deviceDiscovered.connect(self.bt_connect)
        self.scanner.finished.connect(self.scan_timeout)
        self.scanner.start(QtBt.QBluetoothDeviceDiscoveryAgent.DiscoveryMethod.LowEnergyMethod)

    def scan_timeout(self):
        self.scanner.finished.disconnect()
        discovered = self.scanner.discoveredDevices()
        if len(discovered) == 0:
            self.failed.emit('NO_BLE')
            self.finished.emit(False)
            return False

        for dev in discovered:
            print(dev.address().toString(), dev.name())
            if dev.address() == self.mac_object:
                self.progress.emit('DEVICE FOUND!')
                QTimer.singleShot(1, lambda: self.bt_connect(dev))
                return True

        self.progress.emit('DEVICE NOT FOUND')
        self.failed.emit('NO_BLE')
        self.finished.emit(False)
        return False

    def bt_connect(self, dut_obj: QtBt.QBluetoothDeviceInfo):
        if  not self.mac_object or dut_obj.address() != self.mac_object:
            return

        self.scanner.stop()
        self.scanner.deviceDiscovered.disconnect()
        self.progress.emit('DEVICE FOUND!')
        dut_obj.setCached(False)
        dut_obj.setCoreConfigurations(QtBt.QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration)
        
        self.progress.emit('CONNECTING...')
        self.receiver = QtBt.QLowEnergyController.createCentral(dut_obj)
        self.receiver.connected.connect(self.bt_pairing)
        self.receiver.connectToDevice()

    def bt_pairing(self):
        self.progress.emit('PAIRING...')
        self.receiver.connected.disconnect()

        self.local_device = QtBt.QBluetoothLocalDevice()
        if self.local_device.pairingStatus(self.mac_object) == QtBt.QBluetoothLocalDevice.Pairing.Unpaired:
            # print('--- pairing')
            self.local_device.pairingFinished.connect(self.discover_services)
            self.local_device.requestPairing(self.mac_object, QtBt.QBluetoothLocalDevice.Pairing.Paired)
        else:
            # print('--- already paired')
            QTimer.singleShot(10, self.discover_services)

    def discover_services(self):
        self.progress.emit('FINDING SERVICES...')
        try:
            self.local_device.pairingFinished.disconnect()
        except TypeError:
            pass

        self.disc_serv_retry = 2
        self.receiver.discoveryFinished.connect(self.done_disc)
        self.receiver.discoverServices()

    def done_disc(self):
        services = self.receiver.services()
        for serv in services:
            # print(serv.toString())
            if serv.toString() == '{a7b30001-f4c6-4e8a-9c3d-5f2e1d0b8a67}':
                self.wifi_serv = self.receiver.createServiceObject(serv, self)

        if not self.wifi_serv:
            if self.disc_serv_retry > 0:
                self.disc_serv_retry -= 1
                self.receiver.discoverServices()
            else:
                self.receiver.discoveryFinished.disconnect()
                self.progress.emit('SERVICE DISCOVERY FAILED AFTER 3 TIMES')
                self.failed.emit('SERVICE_DISCOVERY_FAIL')
                self.finished.emit(False)
                return

        # QTimer.singleShot(10, self.test_end)
        self.receiver.discoveryFinished.disconnect()
        self.gatt_write_retry = 2
        self.wifi_serv.stateChanged.connect(self.write_gatt)
        self.wifi_serv.discoverDetails(self.wifi_serv.DiscoveryMode.FullDiscovery)

    def write_gatt(self):
        if self.wifi_serv.state() != self.wifi_serv.ServiceState.RemoteServiceDiscovered:
            return

        self.wifi_serv.stateChanged.disconnect()
        self.wifi_serv.errorOccurred.connect(self.gatt_error)
        self.char_dict = {}

        char_list = self.wifi_serv.characteristics()
        # print('characteristics:')
        for char in char_list:
            # print(char.uuid().toString())
            if 'a7b30010' in char.uuid().toString():
                self.char_dict['ssid'] = char
            elif 'a7b30011' in char.uuid().toString():
                self.char_dict['password'] = char
            elif "a7b30012" in char.uuid().toString():
                self.char_dict['mode'] = char
            elif "a7b30013" in char.uuid().toString():
                self.char_dict['conn'] = char
            elif "a7b30014" in char.uuid().toString():
                self.char_dict['ip'] = char

        self.wifi_serv.writeCharacteristic(self.char_dict['ssid'], self.dut_ssid.encode("utf-8"))
        self.wifi_serv.writeCharacteristic(self.char_dict['password'], self.dut_pass.encode("utf-8"))
        self.wifi_serv.writeCharacteristic(self.char_dict['mode'], b"\x01")

        self.wifi_serv.characteristicChanged.connect(self.read_ip)
        self.wifi_serv.writeCharacteristic(self.char_dict['conn'], b"\x04")

        self.progress.emit('WRITING ACCESS POINT DETAILS...')

    def gatt_error(self, err_code):
        error = QtBt.QLowEnergyService.ServiceError
        print('GATT error:', str(err_code))
        
        if err_code == error.CharacteristicWriteError:
            if self.gatt_write_retry > 0:
                print('Retrying write...')
                self.gatt_write_retry -= 1
                self.write_gatt()
                return
        elif err_code == error.CharacteristicReadError:
            if self.gatt_read_retry > 0:
                print('Retrying read...')
                self.gatt_read_retry -= 1
                self.wifi_serv.writeCharacteristic(self.char_dict['conn'], b"\x04")
                return

        self.progress.emit('SERVICE RW FAILED AFTER 3 TIMES')
        self.failed.emit('SERVICE_FAIL')
        self.finished.emit(False)

    def read_ip(self, info: QtBt.QLowEnergyCharacteristic, value):
        # print('--- callback', info.uuid().toString(), 'says', value)
        if info == self.char_dict['conn'] and value == b"\x04":
            self.progress.emit('READING IP ADDRESS...')
            self.gatt_read_retry = 2
            self.wifi_serv.characteristicRead.connect(self.retrieve_ip)
            self.wifi_serv.readCharacteristic(self.char_dict['ip'])
            self.wifi_serv.characteristicChanged.disconnect()

    def retrieve_ip(self, info: QtBt.QLowEnergyCharacteristic, value):
        # print('--- read', info.uuid().toString(), 'says', value)
        if info == self.char_dict['ip']:
            raw_ip = [int.from_bytes(val, 'little') for val in value[:4]]
            raw_ip = [str(val) for val in raw_ip]
            str_ip = '.'.join(raw_ip)

            self.progress.emit(f"FOUND IP: {str_ip}")
            self.found_ip.emit(str_ip)
            self.final_ip_addr = str_ip
            # QTimer.singleShot(1, self.connect_wifi)
            print('--- connecting to device AP')
            self.counter = 0
            self.iface.disconnect()
            self.tmp_profile = self.iface.add_network_profile(self.profile)
            
            self.conn_t.start()
            self.iface.connect(self.tmp_profile)

    def check_connected(self):
        if self.iface.status() == const.IFACE_CONNECTED:
            # self.conn_t.stop()
            self.progress.emit('AP CONNECTED')
            self.finished.emit(True)
            return
        
        if self.iface.status() == const.IFACE_DISCONNECTED:
            self.iface.connect(self.profile)

        self.counter += 1
        print(self.counter)
        if self.counter == self.max_timeout:
            # self.conn_t.stop()
            self.progress.emit('AP FAILED')
            self.failed.emit('WIFI_FAIL')
            self.finished.emit(False)
            return

class ImageAnalysis:
    pass

class HTTPRequest(QObject):
    def __init__(self, parent=None):
        super().__init__()
        self.manager = QNetworkAccessManager(parent)
        self.request = QNetworkRequest()
        self.responses = {}

    def read_response(self, request_id: str) -> QByteArray|None:
        reply_object = self.responses.get(request_id)
        if not reply_object:
            return None
        
        if reply_object.error() == QNetworkReply.NetworkError.NoError:
            byte_content = reply_object.readAll()
            this_obj = self.responses.pop(request_id)
            this_obj.deleteLater()
            return byte_content
        else:
            return None

    def delay_wrapper(self, slot_after, delay):
        QTimer.singleShot(delay, slot_after)
    
    def get(self, req_id: str, url: str, slot_after = None, delay = 10):
        # print('GET Request for', url)
        self.request.setUrl(QUrl.fromUserInput(url))
        net_reply = self.manager.get(self.request)
        self.responses[req_id] = net_reply

        if slot_after:
            if net_reply.isFinished():
                QTimer.singleShot(delay, slot_after)
            else:
                net_reply.finished.connect(lambda: self.delay_wrapper(slot_after, delay))

    def post(self, req_id: str, url: str, slot_after = None, delay = 50):
        # print('POST Request for', url)
        self.request.setUrl(QUrl.fromUserInput(url))
        net_reply = self.manager.post(self.request, None)
        self.responses[req_id] = net_reply

        if slot_after:
            if net_reply.isFinished():
                QTimer.singleShot(delay, slot_after)
            else:
                net_reply.finished.connect(lambda: self.delay_wrapper(slot_after, delay))

    def put(self, req_id: str, url: str, content_type: str, body: str, slot_after = None, delay = 25):
        # print('PUT Request for', url)
        self.request.setUrl(QUrl.fromUserInput(url))
        self.request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, content_type)

        payload = QByteArray(body.encode('utf-8'))
        net_reply = self.manager.put(self.request, payload)
        self.responses[req_id] = net_reply

        if slot_after:
            if net_reply.isFinished():
                QTimer.singleShot(delay, slot_after)
            else:
                net_reply.finished.connect(lambda: self.delay_wrapper(slot_after, delay))

    def abortAll(self):
        for reply_object in self.responses.values():
            try:
                reply_object.finished.disconnect()
            except TypeError:
                pass
            reply_object.abort()
            reply_object.deleteLater()

        self.responses = {}

class Streamer(QThread):
    change_pixmap_signal = pyqtSignal(np.ndarray)
    stream_error = pyqtSignal(str)

    def __init__(self, source=0):
        super().__init__()
        self._run_flag = True
        self.source = source
        self.cap = cv2.VideoCapture(source)

        if not self.cap.isOpened():
            self.cap.release()
            raise cv2.error(f"Cannot open video source {source}")

    def run(self):
        while self._run_flag:
            ret, frame = self.cap.read()
            if ret:
                try:
                    self.change_pixmap_signal.emit(frame)
                except:
                    self.stream_error.emit('STREAM_FAIL')

    def stop(self):
        self._run_flag = False
        self.wait()

class StackedUI(QDialog):
    fail_reason = pyqtSignal(str)
    found_ip = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi(get_path('layout/widget_stack.ui'), self)

        self.dut.setPixmap(QPixmap(get_path(r"static/camera3_vid-0")))
        self.bench_pc.setPixmap(QPixmap(get_path(r"static/computer-2.png")))

        # self.main_thread = QThread()
        self.bt_wifi_thread = QThread(parent=self)
        self.http_manager = HTTPRequest()
        self.stream_widget = Streamer()

        self.widget_list = {self.s0_bled: self.bled_widget,
                            self.s1_indled: self.ind_led_test,
                            self.s2_dft: self.dft_viewer,
                            self.s3_lft80: self.lft_80_viewer,
                            self.s4_lft110: self.lft_110_viewer,
                            self.s5_stream: self.stream_viewer,
                            self.s6_chrg: self.chrg_widget}
        
        self.side_tabs = QButtonGroup()
        for idx, button in enumerate(self.widget_list.keys(), 1):
            self.side_tabs.addButton(button, idx)
        
        self.side_tabs.setExclusive(True)
        self.side_tabs.buttonToggled.connect(self.change_widget)
        self.s0_bled.setChecked(True)

        self._create_circles()

    def set_widget(self, widget_obj):
        if widget_obj:
            self.stacked_ui.setCurrentWidget(widget_obj)
    
    def change_widget(self, button, state):
        if state == True:
            curr_wid = self.widget_list.get(button)
            if curr_wid:
                self.stacked_ui.setCurrentWidget(curr_wid)

    def _create_circles(self):
        self.scene = QGraphicsScene()
        self.scene.setSceneRect(0,0,220,30)
        self.graphicsView.setScene(self.scene)
        self.scene.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.graphicsView.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.graphicsView.setStyleSheet("background: transparent;")
        
        self.circles = []
        self.brush_gray = QBrush(QColor('gray'))
        self.brush_blue = QBrush(QColor('blue'))
        self.brush_green = QBrush(QColor('green'))
        self.brush_red = QBrush(QColor('red'))
        num_circ = 10
        pts = 220 // num_circ

        for i in range(0, num_circ):
            new_ellipse = self.scene.addEllipse(3, 0, 15, 15)
            new_ellipse.setPos(i*pts, 10)
            new_ellipse.setBrush(self.brush_gray)
            new_ellipse.setPen(QPen(Qt.PenStyle.NoPen))
            self.circles.append(new_ellipse)

        self.circ_timer = QTimer(parent=self)
        self.circ_timer.setInterval(200)
        self.circ_timer.timeout.connect(self._run_circles)
        self.curr_idx = 0

    def _run_circles(self):
        curr_brush = self.circles[self.curr_idx].brush()
        if curr_brush == self.brush_gray:
            self.circles[self.curr_idx].setBrush(self.brush_blue)
        elif curr_brush == self.brush_blue:
            self.circles[self.curr_idx].setBrush(self.brush_gray)
        
        self.curr_idx += 1
        if self.curr_idx >= len(self.circles):
            self.curr_idx = 0

    def dut_ap_connect(self, bt_mac):
        self.mac_address = bt_mac
        self.bw_worker = BtWifiWorker(bt_mac, 'lumyolite', 'functest')
        self.bw_worker.moveToThread(self.bt_wifi_thread)

        self.bt_wifi_thread.started.connect(self.bw_worker.bt_scan)
        self.bw_worker.progress.connect(self.progress_msg.setText)
        self.bw_worker.found_ip.connect(self.found_ip.emit)
        self.bw_worker.failed.connect(self.fail_reason.emit)
        self.bw_worker.finished.connect(self.dut_ap_cleanup)

        self.circ_timer.start()
        self.bt_wifi_thread.start()

    def dut_ap_cleanup(self, success):
        if success:
            print('device ip:', self.bw_worker.final_ip_addr)
            self.device_ip = self.bw_worker.final_ip_addr
            for circ_obj in self.circles:
                circ_obj.setBrush(self.brush_green)
        else:
            for circ_obj in self.circles:
                circ_obj.setBrush(self.brush_red)
            return
        
        self.bt_wifi_thread.quit()
        self.circ_timer.stop()
        self.bw_worker.deleteLater()
        unpair_cmd = ['daq/BluetoothDevicePairing.exe', 'unpair-by-mac', '--mac', self.mac_address, '--type', 'BluetoothLE']
        resp_unpair = subprocess.run(unpair_cmd, stdout=subprocess.DEVNULL)

        self.status_retry = 2
        self.http_manager.get('status', 
                                f'http://{self.device_ip}/status', 
                                self.read_dut_status,
                                1500)

    def read_dut_status(self):
        content = self.http_manager.read_response('status')
        if content:
            device_status = json.loads(str(content, 'utf-8'))
        elif self.status_retry > 0:
            self.status_retry -= 1
            self.http_manager.get('status', 
                                f'http://{self.device_ip}/status', 
                                self.read_dut_status,
                                1500)
            return
        else:
            self.progress_msg.setText('STATUS READ FAILED AFTER 3 TIMES')
            return

        tab_model = QStandardItemModel()
        self.tableView.setModel(tab_model)
        for key, value in device_status.items():
            ll = QStandardItem(key)
            rr = QStandardItem(str(value))
            tab_model.appendRow([ll, rr])
        self.tableView.setColumnWidth(0, 180)
        self.tableView.setColumnWidth(1, 253)

    def stream_start(self, link: str):
        self.s5_stream.setChecked(True)
        cam_name = link.split('=')[-1]
        self.camera_name_sv.setText(cam_name.upper())
        self.stacked_ui.setCurrentWidget(self.stream_viewer)
        try:
            self.vthread = Streamer(link)
            self.vthread.change_pixmap_signal.connect(self.update_image)
            self.vthread.stream_error.connect(self.fail_reason.emit)
            self.vthread.start()
            QTimer.singleShot(10 * 1000, self.stream_stop)
        except:
            self.fail_reason.emit('STREAM_FAIL')

    def stream_stop(self):
        self.done_stream.emit(self.camera_name_sv.text().lower())
        try:
            self.vthread.stream_error.disconnect()
            self.vthread.change_pixmap_signal.disconnect()
        except:
            pass
        self.vthread.stop()
        # self.vthread.deleteLater()

    def onTop(self):
        if not self.isVisible():
            self.show()
        if not self.isActiveWindow():
            self.activateWindow()
        self.raise_()
    
    def closeEvent(self, event):
        if self.parent():
            event.ignore()
            self.hide()

if __name__=='__main__':
    import sys
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')

    stacker = StackedUI()
    stacker.show()
    stacker.dut_ap_connect('CC:C8:37:2F:8B:29') # fresh

    sys.exit(app.exec())