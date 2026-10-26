import cv2
import numpy as np

def main(cap):
    for i in range(10):
        ret, frame = cap.read()
    cv2.imshow('test', frame)

    channel_sums = np.sum(frame, axis=(0,1))   
    blue_sum = channel_sums[0]
    green_sum = channel_sums[1]
    red_sum = channel_sums[2]

    h, w = frame.shape[:2]
    area = h*w
    print(f"R: {(red_sum / area):.2f} G: {(green_sum / area):.2f} B: {(blue_sum / area):.2f}")

    r2g_ratio = red_sum / green_sum
    b2g_ratio = blue_sum / green_sum

    print(f'R/G = {r2g_ratio:.3f}')
    print(f'B/G = {b2g_ratio:.3f}')

def other_main():
    cap = cv2.VideoCapture(0)
    while True:
        inp = input(':P')
        main(cap)

        if cv2.waitKey(0) == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

def mtf_test():
    import matplotlib.pyplot as plt
    from pylinac.core.mtf import EdgeSpreadFunctionMTF

    img = cv2.imread(r"usaf-test.jpg")[373:858, 715:1251]
    grayscale = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    edge_1 = grayscale[195:227, 267]
    edge_2 = grayscale[195:227, 274]
    edge_3 = grayscale[195:227, 289]
    edge_4 = grayscale[195:227, 297]

    mtf_calc = EdgeSpreadFunctionMTF(esf=[edge_1, edge_2, edge_3, edge_4], padding_mode='auto')
    values = mtf_calc.plot()
    xydata = values[0].get_xydata().tolist()

    found_idx = []
    for x, y in xydata:
        if y > 0.49 and y < 0.51: 
            found_idx.append((x,y))

    print(found_idx)
    plt.show()

def http_api_test():
    import requests
    import json
    import cv2
    camera = cv2.VideoCapture(2)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    device_ip = '192.168.4.1'
    # device_ip = input('ip:')
    while True:
        inp = input('cmd:')
        if 'set ip' in inp:
            _, _, device_ip = inp.split()
            print('--- New IP Address:', device_ip)
        elif inp == 'led on':
            resp = requests.put('http://' + device_ip + '/torch-mode',
                                headers={'content-type': 'text/plain', 'content-length': '5'},
                                data='white')
            print('---', resp.status_code, resp.content.decode())
        elif inp == 'uv on':
                resp = requests.put('http://' + device_ip + '/torch-mode', 
                                    headers={'content-type': 'text/plain', 'content-length': '11'}, 
                                    data='fluorescent')
                print('---', resp.status_code, resp.content.decode()) 
        elif inp == 'led off':
            resp = requests.put('http://' + device_ip + '/torch-mode', 
                                headers={'content-type': 'text/plain', 'content-length': '3'}, 
                                data='off')
            print('---', resp.status_code, resp.content.decode()) 
        elif 'red' in inp or 'green' in inp or 'blue' in inp or 'orange' in inp:
            color, state = inp.split()

            flag = 'true' if state == 'on' else 'false'
            ui_led_ctrl = {"color": color, "on": flag}

            resp = requests.put('http://' + device_ip + '/status-led', 
                                headers={'content-type': 'application/json'}, 
                                data=json.dumps(ui_led_ctrl))
            print('---', resp.status_code, resp.content.decode())
        elif 'cap' in inp:
            ret, frame = camera.read()
            cv2.imshow('', frame)
            cv2.waitKey(0)
        elif 'end test' in inp:
            break
        else:
            print('--- invalid command')
            pass

if __name__=="__main__":
    # other_main()
    http_api_test()
    # mtf_test()