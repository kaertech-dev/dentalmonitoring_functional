import os
import sys
import cv2
import pywifi
import numpy as np
from datetime import datetime
from pywifi import const

import PyQt6.QtBluetooth as QtBt
from PyQt6.QtWidgets import QApplication, QDialog, QGraphicsScene, QWidget, QButtonGroup, QGraphicsRectItem, QGraphicsLineItem
from PyQt6.QtGui import QPen, QColor, QPixmap, QImage, QBrush, QStandardItemModel, QStandardItem
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl, QObject, QByteArray
from PyQt6 import uic

_cv2cap = {}
def SingletonCamera(src: int) -> cv2.VideoCapture:
    global _cv2cap
    if src not in _cv2cap:
        _cv2cap[src] = cv2.VideoCapture(src, cv2.CAP_DSHOW)
    return _cv2cap[src]

def get_path(raw_path):
    try:
        ret = os.path.join(sys._MEIPASS, raw_path)
    except Exception:
        ret = os.path.join('.', raw_path)
    return ret

class Bluetooth(QWidget):
    ip_addr_found = pyqtSignal(str)
    progress_status = pyqtSignal(str)
    fail_reason = pyqtSignal(str)

    def __init__(self, ssid, password, parent=None):
        super().__init__(parent)
        # self.parent = parent
        # uic.loadUi(get_path('layout/bt_wid.ui'), self)

        # self.mac_object = QtBt.QBluetoothAddress(mac_address)
        self.scanner = QtBt.QBluetoothDeviceDiscoveryAgent(self)
        self.scanner.setLowEnergyDiscoveryTimeout(30000)
        self.scanner.errorOccurred.connect(self.scanner_error)
        self.scanner.deviceDiscovered.connect(self.dut_connect)
        self.scanner.finished.connect(self.scan_timeout)
        
        self.local_device = QtBt.QBluetoothLocalDevice(self)
        self.local_device.errorOccurred.connect(self.device_error)
        self.receiver = None
        self.wifi_serv = None
        self.retries = 3

        self.dut_ssid = ssid
        self.dut_pass = password
        self.final_ip_addr = ''
        
        self.scene = QGraphicsScene()
        self.scene.setSceneRect(0,0,220,30)
        self.parent().graphicsView.setScene(self.scene)
        self.scene.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.parent().graphicsView.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.parent().graphicsView.setStyleSheet("background: transparent;")
        
        self.circles = []
        self.brush_gray = QBrush(QColor('gray'))
        self.brush_blue = QBrush(QColor('blue'))
        self.brush_green = QBrush(QColor('green'))
        num_circ = 10
        pts = 220 // num_circ

        for i in range(0, num_circ):
            new_ellipse = self.scene.addEllipse(3, 0, 15, 15)
            new_ellipse.setPos(i*pts, 10)
            new_ellipse.setBrush(self.brush_gray)
            new_ellipse.setPen(QPen(Qt.PenStyle.NoPen))
            self.circles.append(new_ellipse)

        self.timer = QTimer()
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.run_circles)
        self.curr_idx = 0
        # self.timer.start()
        self.auto_abort_timer = QTimer()
        self.auto_abort_timer.setInterval(1000 * 60) # 1 minute timeout
    
    def set_mac(self, mac_address):
        self.mac_object = QtBt.QBluetoothAddress(mac_address)

    def auto_abort(self):
        # self.parent().progress_msg.setText('BLE FAIL')
        # self.fail_reason.emit('NO_BLE')
        # self.abortAll()
        pass
    
    def abortAll(self):
        self.timer.stop()
        self.process_end()

        try:
            self.scanner.finished.disconnect()
            self.receiver.connected.disconnect()
            self.receiver.discoveryFinished.disconnect()
            self.local_device.pairingFinished.disconnect()

            if self.wifi_serv:
                self.wifi_serv.stateChanged.disconnect()
                self.wifi_serv.characteristicChanged.disconnect()
                self.wifi_serv.characteristicRead.disconnect()
        except TypeError:
            pass
        except AttributeError:
            pass

        for circ in self.circles:
            circ.setBrush(self.brush_gray)

    def process_end(self):
        self.retries = 3
        self.timer.stop()
        self.auto_abort_timer.stop()
        for circ_obj in self.circles:
            circ_obj.setBrush(self.brush_green)

        self.scanner.stop()
        # self.local_device.requestPairing(self.mac_object, QtBt.QBluetoothLocalDevice.Pairing.Paired)
        try:
            if self.receiver:
                self.receiver.disconnectFromDevice()
        except TypeError:
            pass

    def run_circles(self):
        curr_brush = self.circles[self.curr_idx].brush()
        if curr_brush == self.brush_gray:
            self.circles[self.curr_idx].setBrush(self.brush_blue)
        elif curr_brush == self.brush_blue:
            self.circles[self.curr_idx].setBrush(self.brush_gray)
        
        self.curr_idx += 1
        if self.curr_idx >= len(self.circles):
            self.curr_idx = 0

    def gatt_error(self, err_code):
        print('GATT error:', str(err_code.value))
        # self.fail_reason.emit('NO_BLE')
        self.progress_status.emit(str(err_code.value))
        if self.retries > 0:
            print('Retrying...')
            self.write_gatt()

    def device_error(self, err_code):
        print('DUT error:', str(err_code.value))
        # self.fail_reason.emit('NO_BLE')
        self.progress_status.emit(str(err_code.value))

    def controller_error(self):
        print('receiver error:', str(self.receiver.error()))
        # self.fail_reason.emit('NO_BLE')
        self.progress_status.emit(str(self.receiver.error()))    

    def scanner_error(self):
        print('scan error:', str(self.scanner.error()))
        # self.fail_reason.emit('NO_BLE')
        self.progress_status.emit(str(self.scanner.error()))

    def write_ip(self, info: QtBt.QLowEnergyCharacteristic, value):
        self.auto_abort_timer.start()
        # print('--- read', info.uuid().toString(), 'says', value)
        if info == self.char_dict['ip']:
            raw_ip = [int.from_bytes(val, 'little') for val in value[:4]]
            raw_ip = [str(val) for val in raw_ip]
            str_ip = '.'.join(raw_ip)

            self.final_ip_addr = str_ip
            self.parent().progress_msg.setText(f"FOUND IP: {str_ip}")
            self.ip_addr_found.emit(str_ip)
            QTimer.singleShot(0, self.process_end)

    def get_ip(self, info: QtBt.QLowEnergyCharacteristic, value):
        self.auto_abort_timer.start()
        # print('--- callback', info.uuid().toString(), 'says', value)
        if info == self.char_dict['conn'] and value == b"\x04":
            self.parent().progress_msg.setText('READING IP ADDRESS...')
            self.progress_status.emit('Reading IP Address...')
            self.wifi_serv.characteristicRead.connect(self.write_ip)
            self.wifi_serv.readCharacteristic(self.char_dict['ip'])
            self.wifi_serv.characteristicChanged.disconnect()

    def write_gatt(self):
        self.auto_abort_timer.start()
        # print(self.wifi_serv.state())
        self.retries -= 1
        if self.wifi_serv.state() != self.wifi_serv.ServiceState.RemoteServiceDiscovered:
            return

        self.wifi_serv.stateChanged.disconnect()
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
        # self.wifi_serv.writeCharacteristic(self.char_dict['mode'], b"\x00")
        self.wifi_serv.writeCharacteristic(self.char_dict['mode'], b"\x01") # We're now using AP Mode

        self.wifi_serv.characteristicChanged.connect(self.get_ip)
        self.wifi_serv.errorOccurred.connect(self.gatt_error)
        # self.wifi_serv.writeCharacteristic(self.char_dict['conn'], b"\x01")
        self.wifi_serv.writeCharacteristic(self.char_dict['conn'], b"\x04") # Apply ssid and password

        self.parent().progress_msg.setText('WRITING ACCESS POINT DETAILS...')
        self.progress_status.emit('Writing AP Mode details...')

    def done_disc(self):
        self.auto_abort_timer.start()
        # print('services:')
        services = self.receiver.services()
        for serv in services:
            # print(serv.toString())
            if serv.toString() == '{a7b30001-f4c6-4e8a-9c3d-5f2e1d0b8a67}':
                self.wifi_serv = self.receiver.createServiceObject(serv, self)

        if not self.wifi_serv:
            return

        # QTimer.singleShot(10, self.test_end)
        self.wifi_serv.stateChanged.connect(self.write_gatt)
        self.wifi_serv.discoverDetails(self.wifi_serv.DiscoveryMode.FullDiscovery)

    def discover_services(self):
        self.auto_abort_timer.start()
        # print(self.local_device.connectedDevices())
        # stat = self.local_device.pairingStatus(self.mac_object)
        # print('paired?', stat)

        # print('--- service discovery')
        self.parent().progress_msg.setText('FINDING SERVICES...')
        self.progress_status.emit('Finding services...')
        self.receiver.discoveryFinished.connect(self.done_disc)
        self.receiver.discoverServices()

    def dut_pairing(self):
        self.auto_abort_timer.start()
        self.parent().progress_msg.setText('PAIRING...')
        self.progress_status.emit('Pairing...')
        if self.local_device.pairingStatus(self.mac_object) == QtBt.QBluetoothLocalDevice.Pairing.Unpaired:
            # print('--- pairing')
            self.local_device.pairingFinished.connect(self.discover_services)
            self.local_device.requestPairing(self.mac_object, QtBt.QBluetoothLocalDevice.Pairing.Paired)
        else:
            # print('--- already paired')
            QTimer.singleShot(10, self.discover_services)

    def dut_connect(self, dut_obj: QtBt.QBluetoothDeviceInfo):
        self.auto_abort_timer.start()
        print('found = ', dut_obj.address().toString())
        if  not self.mac_object or dut_obj.address() != self.mac_object:
            return

        self.scanner.stop()
        self.parent().progress_msg.setText('DEVICE FOUND!')
        dut_obj.setCached(False)
        dut_obj.setCoreConfigurations(QtBt.QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration)

        self.receiver = QtBt.QLowEnergyController.createCentral(dut_obj, self)
        self.receiver.errorOccurred.connect(self.controller_error)
        
        self.parent().progress_msg.setText('CONNECTING...')
        self.progress_status.emit('Connecting to device...')
        self.receiver.connected.connect(self.dut_pairing)
        self.receiver.connectToDevice()

    def scan_timeout(self):
        discovered = self.scanner.discoveredDevices()
        if len(discovered) == 0:
            return

        for dev in discovered:
            print(dev.address().toString(), dev.name())
            if dev.address() == self.mac_object:
                self.parent().progress_msg.setText('DEVICE FOUND!')
                QTimer.singleShot(1, lambda: self.dut_connect(dev))
                return True

        self.fail_reason.emit('NO_BLE')
        return False

    def dut_scanning(self):
        self.curr_idx = 0
        for circ_obj in self.circles:
            circ_obj.setBrush(self.brush_gray)

        self.timer.start()
        self.auto_abort_timer.start()
        self.final_ip_addr = ''
        self.parent().progress_msg.setText('SCANNING FOR DEVICE...')
        self.progress_status.emit('Scanning for device...')
        self.scanner.start(QtBt.QBluetoothDeviceDiscoveryAgent.DiscoveryMethod.LowEnergyMethod)

class ChargingWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.scene = QGraphicsScene()
        self.scene.setSceneRect(0,0,710,320)
        self.parent().main_view.setScene(self.scene)
        self.scene.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.parent().main_view.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))

        self.brush_green = QBrush(QColor(70, 255, 20))

        red_pen = QPen(QColor('red'))
        red_pen.setWidth(3)

        blk_pen = QPen(QColor('black'))
        blk_pen.setWidth(3)

        self.positive = [QGraphicsLineItem(110, 160, 110, 300),
                            QGraphicsLineItem(110, 300, 685, 300),
                            QGraphicsLineItem(685, 300, 685, 160)]
        for pos in self.positive:
            pos.setPen(red_pen)
            self.scene.addItem(pos)

        self.negative = [QGraphicsLineItem(330, 160, 330, 280),
                            QGraphicsLineItem(330, 280, 635, 280),
                            QGraphicsLineItem(635, 280, 635, 160)]
        for neg in self.negative:
            neg.setPen(blk_pen)
            self.scene.addItem(neg)

    def set_old_level(self, percent):
        try:
            self.scene.removeItem(self.uncharged)
            self.scene.removeItem(self.charged)
        except:
            pass

        batt_percent = int(160 * (percent/100))
        if percent > 0:
            # print('batt level', percent, 'height', batt_percent)
            self.uncharged = QGraphicsRectItem(0, 0, 100, 160-batt_percent)
            self.scene.addItem(self.uncharged)
            self.uncharged.setPos(610, 0)
            self.uncharged.setPos(610,0)

        self.charged = QGraphicsRectItem(0, 0, 100, batt_percent)
        self.scene.addItem(self.charged)
        self.charged.setPos(610, 160-batt_percent)
        self.charged.setBrush(self.brush_green)
        # self.charged.setPen(QPen(Qt.PenStyle.NoPen))

class HTTPRequest(QObject):
    def __init__(self, parent=None):
        super().__init__()
        self.manager = QNetworkAccessManager(parent)
        self.request = QNetworkRequest()
        self.responses = {}

    def read_response(self, request_id: str) -> QByteArray|None:
        # print('======')
        # print('Request Objects:')
        # for id, resp in self.responses.items():
        #     print(id, '---', resp.url().toString())
        #     print('-----', resp.isRunning())
        # print('======')
        
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

class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(np.ndarray)
    stream_error = pyqtSignal(str)

    def __init__(self, source=0):
        super().__init__()
        self._run_flag = True
        self.source = source
        if type(source) == int:
            self.cap = SingletonCamera(source)
        else:
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

class WifiThread(QObject):
    conn_success = pyqtSignal(bool)
    net_restored = pyqtSignal(bool)
    fail_reason = pyqtSignal(str)
    def __init__(self, ssid, passkey):
        super().__init__()

        wifi = pywifi.PyWiFi()
        self.iface = wifi.interfaces()[0]

        self.default_profile = None
        for prof in self.iface.network_profiles():
            if prof.ssid == 'Kaertech':
                self.default_profile = prof
                break

        self.profile = pywifi.Profile()
        self.profile.ssid = ssid
        self.profile.auth = const.AUTH_ALG_OPEN
        self.profile.akm.append(const.AKM_TYPE_WPA2PSK)
        self.profile.cipher = const.CIPHER_TYPE_CCMP
        self.profile.key = passkey

        check_interval = 500
        self.conn_t = QTimer()
        self.conn_t.setInterval(check_interval)
        self.conn_t.timeout.connect(self.check_connected)
        # self.restore_t = QTimer()
        # self.restore_t.setInterval(check_interval)
        # self.restore_t.timeout.connect(self.check_restore)

        self.max_timeout = (1000/check_interval) * 30
        self.counter = 0
        # self.restored = False

    # def restore_net(self):
    #     self.counter = 0
    #     self.restore_t.start()
    #     self.iface.connect(self.default_profile)

    # def check_restore(self):
    #     if self.iface.status() == const.IFACE_CONNECTED:
    #         self.restore_t.stop()
    #         print('Net restored:', self.default_profile.ssid)
    #         self.net_restored.emit(True)
    #         self.restored = True
    #         return
        
    #     if self.iface.status() == const.IFACE_DISCONNECTED:
    #         self.iface.connect(self.profile)

    #     self.counter += 1
    #     # print('conn', self.counter)
    #     if self.counter == self.max_timeout:
    #         self.restore_t.stop()
    #         print('Net not restored')
    #         self.net_restored.emit(False)
    #         return

    def run(self, ip_addr):
        print('--- wifi thread running')
        self.counter = 0
        self.iface.disconnect()
        QTimer.singleShot(100, self.start_conn)

    def start_conn(self):
        self.restored = False
        self.tmp_profile = self.iface.add_network_profile(self.profile)
        
        self.conn_t.start()
        self.iface.connect(self.profile)
        # QTimer.singleShot(500, lambda: self.conn_success.emit(True))
    
    def maintain_conn(self):
        if self.iface.status() == const.IFACE_CONNECTED:
            print('----- still connected')
        else:
            self.iface.connect(self.profile)

    def forget_network(self):
        self.iface.disconnect()
        self.iface.remove_network_profile(self.profile)

        self.counter = 0
        self.restore_t.start()
        self.iface.connect(self.default_profile)

    def check_connected(self):
        if self.iface.status() == const.IFACE_CONNECTED:
            self.conn_t.stop()
            print('AP connected')
            self.conn_success.emit(True)
            return
        
        if self.iface.status() == const.IFACE_DISCONNECTED:
            self.iface.connect(self.profile)

        self.counter += 1
        # print('conn', self.counter)
        if self.counter == self.max_timeout:
            self.conn_t.stop()
            print('AP failed')
            self.conn_success.emit(False)
            self.fail_reason.emit('WIFI_FAIL')
            return

class StackedUI(QDialog):
    done_measure = pyqtSignal(str, str, int)
    led_okay = pyqtSignal(float)
    done_stream = pyqtSignal(str)
    fail_reason = pyqtSignal(str)
    alt_signal = pyqtSignal(dict)

    def __init__(self, duration, cam_source, cam_params, test_limits, dut_ssid, dut_pass):
        super().__init__()
        uic.loadUi(get_path('layout/widget_stack.ui'), self)

        self.dut.setPixmap(QPixmap(get_path(r"static/camera3_vid-0")))
        self.bench_pc.setPixmap(QPixmap(get_path(r"static/computer-2.png")))

        self.bt_widget = Bluetooth(dut_ssid, dut_pass, self)
        self.wifi_widget = WifiThread(dut_ssid, dut_pass)
        self.bt_widget.ip_addr_found.connect(self.wifi_widget.run)
        self.bt_widget.fail_reason.connect(self.fail_reason.emit)
        self.wifi_widget.fail_reason.connect(self.fail_reason.emit)

        self.ui_led_results = {}
        self.test_limits = test_limits

        self.camera = SingletonCamera(cam_source)
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self.camera.set(cv2.CAP_PROP_SATURATION, 256)
        if cam_params:
            self.camera.set(cv2.CAP_PROP_EXPOSURE, cam_params['exposure'])
            self.camera.set(cv2.CAP_PROP_FOCUS, cam_params['focus'])
            self.camera.set(cv2.CAP_PROP_ZOOM, cam_params['zoom'])

        self.vthread = VideoThread(cam_source)
        # self.stream_timeout = QTimer()
        # self.stream_timeout.timeout.connect(self.stream_stop)
        # self.stream_timeout.setInterval(duration*1000)
        self.stream_dur = duration

        self.charging_widget = ChargingWidget(self)

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

    def set_widget(self, widget_obj):
        if widget_obj:
            self.stacked_ui.setCurrentWidget(widget_obj)
    
    def change_widget(self, button, state):
        if state == True:
            curr_wid = self.widget_list.get(button)
            if curr_wid:
                self.stacked_ui.setCurrentWidget(curr_wid)

    def abort_test(self):
        self.reset_holders()
        try:
            self.bt_widget.abortAll()
            self.wifi_widget.forget_network()
            self.done_measure.disconnect()
            self.done_stream.disconnect()
            if self.vthread.isRunning():
                self.vthread.change_pixmap_signal.disconnect(self.update_image)
                self.vthread.stop()

        except TypeError:
            pass

    def start_bt_scan(self, mac_address):
        if self.tableView.model():
            to_del = self.tableView.model()
            to_del.deleteLater()
        
        self.s0_bled.setChecked(True)
        self.bt_widget.set_mac(mac_address)
        self.bt_widget.dut_scanning()

    def show_status(self, device_status: dict):
        tab_model = QStandardItemModel()
        self.tableView.setModel(tab_model)
        for key, value in device_status.items():
            ll = QStandardItem(key)
            rr = QStandardItem(str(value))
            tab_model.appendRow([ll, rr])
        self.tableView.setColumnWidth(0, 180)
        self.tableView.setColumnWidth(1, 255)

    def check_color(self, raw, color_name):
        image = raw[290:790, 710:1210]
        blue_hist = cv2.calcHist([image], [0], None, [256], [0,256])
        green_hist = cv2.calcHist([image], [1], None, [256], [0,256])
        red_hist = cv2.calcHist([image], [2], None, [256], [0,256])
    
        weights = [i for i in range(0,256)]
        blue_ave = np.average(blue_hist, weights=weights).item()
        green_ave = np.average(green_hist, weights=weights).item()
        red_ave = np.average(red_hist, weights=weights).item()
        total = (blue_ave + red_ave + green_ave)

        if total == 0:
            return 0, '0,0,0'
        
        red = round(100 * red_ave / total, 2)
        green = round(100 * green_ave / total, 2)
        blue = round(100 * blue_ave / total, 2)
        print(f"{color_name} = {red:.5f}, {green:.5f}, {blue:.5f}")

        limit = self.test_limits['indicator_led'].get(color_name)
        red_l, green_l, blue_l = limit.split(',')

        if red >= float(red_l) and green >= float(green_l) and blue >= float(blue_l):
            return 1, f"{red},{green},{blue}"
        else:
            return 0, f"{red},{green},{blue}"

    def add_ind_led(self, color):
        if self.stacked_ui.currentWidget() != self.ind_led_test:
            self.ui_led_results = {}
            self.s1_indled.setChecked(True)

        for i in range(10):
            ret, cv_img = self.camera.read()

        cv2.imwrite(f'{color}.jpg', cv_img)
        
        h, w, ch = cv_img.shape
        bytes_per_line = ch * w
        qt_img = QImage(cv_img.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)
        qt_img = qt_img.scaledToHeight(220, Qt.TransformationMode.SmoothTransformation)

        if color == 'red':
            self.red_img.setPixmap(QPixmap.fromImage(qt_img))
        elif color == 'green':
            self.green_img.setPixmap(QPixmap.fromImage(qt_img))
        elif color == 'blue':
            self.blue_img.setPixmap(QPixmap.fromImage(qt_img))
        elif color == 'orange':
            self.orange_img.setPixmap(QPixmap.fromImage(qt_img))

        # _, clipped = cv2.threshold(cv_img, 200, 255, cv2.THRESH_TRUNC)
        # cv2.imwrite(f"{color}.jpg", clipped)
        return self.check_color(cv_img, color)

    def bytesToImage(self, url_resp_content):
        arr = np.asarray(url_resp_content, dtype=np.uint8)
        return cv2.imdecode(arr, -1)

    def update_image(self, cv_img):
        h, w, ch = cv_img.shape
        bytes_per_line = ch * w
        qt_img = QImage(cv_img.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)
        self.strm_pixlabel.setPixmap(QPixmap.fromImage(qt_img))

    def reset_holders(self):
        self.red_img.clear()
        self.green_img.clear()
        self.blue_img.clear()
        self.orange_img.clear()

        self.dft_pixlabel.clear()
        self.lft_80_pixlabel.clear()
        self.lft_110_pixlabel.clear()

        self.strm_pixlabel.clear()

    def dft_start(self, serial_num, cam_name, content):
        self.camera_name_dft.setText(cam_name.upper())
        self.s2_dft.setChecked(True)
        pixmap = QPixmap()
        pixmap.loadFromData(content)
        self.dft_pixlabel.setPixmap(pixmap)
        raw_image = self.bytesToImage(content)
        
        cv2.imwrite(f'photos/{serial_num}_dark.jpg', raw_image)
        
        grayscale = cv2.cvtColor(raw_image, cv2.COLOR_BGR2GRAY)
        mono_hist = cv2.calcHist([grayscale], [0], None, [256], [0,256])
        
        # measure luminance
        average_luminance = np.average(grayscale, axis=(0,1)).item()
        
        # measure hot pixels
        hot_pixels = int(mono_hist[255])

        # snr
        mean_val, std_dev = cv2.meanStdDev(grayscale)
        mean_val = mean_val[0][0]
        std_dev = std_dev[0][0]

        if std_dev != 0:
            snr_db = 20 * np.log10(mean_val / std_dev)
        else:
            snr_db = float('inf')

        # self.df_results = {'lum': round(average_luminance, 2), 'snr': round(snr_db.item(), 2)}
        
        alt_results = {'df_lumi': round(average_luminance, 2), 'df_snr': round(snr_db.item(), 2)}
        self.alt_signal.emit(alt_results)
        dft_status = self.get_final_status('dark_frame_test', alt_results)
        self.done_measure.emit(cam_name, 'dark', dft_status)

    def lft_start(self, serial_num, cam_name, distance, content):
        pixmap = QPixmap()
        pixmap.loadFromData(content)
        raw_image = self.bytesToImage(content)

        if distance == 80:
            cv2.imwrite(f'photos/{serial_num}_lf80.jpg', raw_image)
            self.camera_name_lft80.setText(cam_name.upper())
            self.s3_lft80.setChecked(True)
            self.lft_80_pixlabel.setPixmap(pixmap)
        elif distance == 110:
            cv2.imwrite(f'photos/{serial_num}_lf110.jpg', raw_image)
            self.camera_name_lft110.setText(cam_name.upper())
            self.s4_lft110.setChecked(True)
            self.lft_110_pixlabel.setPixmap(pixmap)

        grayscale = cv2.cvtColor(raw_image, cv2.COLOR_BGR2GRAY)
        if distance == 110:
            average_luminance = np.average(grayscale, axis=(0,1)).item()
            self.led_okay.emit(average_luminance)
        
        mono_hist = cv2.calcHist([grayscale], [0], None, [256], [0,256])
        # blue_hist = cv2.calcHist([self.raw_image], [0], None, [256], [0,256])
        # green_hist = cv2.calcHist([self.raw_image], [1], None, [256], [0,256])
        # red_hist = cv2.calcHist([self.raw_image], [2], None, [256], [0,256])

        # measure dead pixels
        # num_dead_pixels = mono_hist[0].item()
        num_dead_pixels = -1

        # align the image using dot grid
        # rot_angle = self.getAngle(self.raw_image)
        # adjusted = self.alignImage(self.raw_image, rot_angle)

        # get maximum and minimum pixel value
        min_int = np.amin(grayscale).item()
        max_int = np.amax(grayscale).item()

        # Michelson contrast
        m_cont = round((max_int - min_int) / (max_int + min_int), 4)
        # !! if minimum = zero since it would always return a value of 1.0

        # color ratio
        channel_sums = np.sum(raw_image, axis=(0,1))
        blue_sum = channel_sums[0].item()
        green_sum = channel_sums[1].item()
        red_sum = channel_sums[2].item()
        r2g_ratio = round(red_sum / green_sum, 4)
        b2g_ratio = round(blue_sum / green_sum, 4)

        # Sector Balance

        # get resolution in lp/mm
        # group = self.reso_grp_input.value()
        # element = self.reso_elem_input.value()
        # resolution = 2**(group + ((element-1)/6))

        # calculate mtf50 = 100% * Cf/Co, where C = (Vmax - Vmin)/(Vmax + Vmin)
        # Co = Vmax & Vmin at low-frequency (i.e. clear sharp edge)
        # Cf = Vmax & Vmin where the mtf50 value is 50%

        mean_val, std_dev = cv2.meanStdDev(grayscale)
        mean_val = mean_val[0][0]
        std_dev = std_dev[0][0]

        if std_dev != 0:
            snr_db = 20 * np.log10(mean_val / std_dev)
        else:
            snr_db = float('inf')

        # self.lf_results = {'resolution': -1, 'light_balance': -1, 'contrast': m_cont, 
        #                     'r2g_ratio': r2g_ratio, 'b2g_ratio': b2g_ratio, 
        #                     'snr': round(snr_db.item(), 2), 'dead_pixels': num_dead_pixels, 'dot_count': -1}
        
        if distance == 80:
            alt_results = {'lf80_reso': -1, 'lf80_contrast': m_cont, 
                                'lf80_r2g': r2g_ratio, 'lf80_b2g': b2g_ratio, 
                                'lf80_snr': round(snr_db.item(), 2), 'lf80_dotgrid': -1}
        elif distance == 110:
            alt_results = {'lf110_reso': -1, 'lf110_contrast': m_cont, 
                                'lf110_r2g': r2g_ratio, 'lf110_b2g': b2g_ratio, 
                                'lf110_snr': round(snr_db.item(), 2), 'lf110_dotgrid': -1}

        self.alt_signal.emit(alt_results)
        lft_status = self.get_final_status(f'lit_frame_{distance}mm', alt_results)
        self.done_measure.emit(cam_name, f'lit_{distance}', lft_status)

    def get_final_status(self, test_name: str, raw_values: dict):
        params_list = self.test_limits.get(test_name)
        status = 1
        for key, value in raw_values.items():
            param_name = key.split('_')[-1]
            limits = params_list.get(param_name)
            print(key, value, limits)
            if not limits:
                continue

            if 'maximum' in limits:
                if value > float(limits['maximum']):
                    status -= 1
            if 'minimum' in limits:
                if value < float(limits['minimum']):
                    status -= 1
        
        if status < 0:
            status = 0
        return status

    def stream_start(self, link: str):
        self.s5_stream.setChecked(True)
        cam_name = link.split('=')[-1]
        self.camera_name_sv.setText(cam_name.upper())
        self.stacked_ui.setCurrentWidget(self.stream_viewer)
        try:
            self.vthread = VideoThread(link)
            self.vthread.change_pixmap_signal.connect(self.update_image)
            self.vthread.stream_error.connect(self.fail_reason.emit)
            self.vthread.start()
            # self.stream_timeout.start()
            QTimer.singleShot(self.stream_dur * 1000, self.stream_stop)
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
    stacker.bt_widget.set_mac('CC:C8:37:2F:8B:29') # fresh
    stacker.bt_widget.dut_scanning()

    # altbt = AltBT('COM7')
    # wifi_t = WifiThread()

    # altbt.found_ip.connect(wifi_t.run)
    # altbt.set_mac('CC:C8:37:2F:69:80')

    # new_thread = QThread()
    # altbt.moveToThread(new_thread)
    # wifi_t.moveToThread(new_thread)
    # new_thread.started.connect(altbt.connect)

    # new_thread.start()

    sys.exit(app.exec())