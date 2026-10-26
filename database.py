import os
import csv
import pymysql
from pymysql.cursors import DictCursor
from datetime import datetime, time, timedelta

DB_USER = os.getenv('DB_USER', 'labeling')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'labeling')

DB_CONFIG = {
    'user': DB_USER,
    'password': DB_PASSWORD,
    'autocommit': False,
    'use_unicode': True,
    'charset': 'utf8mb4'
}

class DatabaseConn:
    def __init__(self, db_host: str, schema: str, model: str, station: str):
        self.config = DB_CONFIG
        self.config['host'] = db_host
        self.schema = schema
        self.model = model
        self.station = station
        self.table_name = f"`{model}_{station}`"
        self.previous_stations = []
        self.csv_headers = []

    def connect(self, schema: str):
        """Create database connection"""
        try:
            self.config['database'] = schema
            return pymysql.connect(**self.config)
        except pymysql.Error as e:
            # raise Exception(f"Database connection error: {e}")
            print(f"Database connection error: {e}")
            return None

    def get_golden_logs(self, tester_code) -> tuple[int, str]:
        try:
            with self.connect('te') as conn:
                with conn.cursor(DictCursor) as cursor:
                    today = datetime.now()
                    am = time(7,0,0)
                    pm = time(19,0,0)
                    if today.time() >= am and today.time() < pm:
                        shift = [datetime.combine(today.date(), am), datetime.combine(today.date(), pm)]
                    else:
                        shift = [datetime.combine(today.date(), pm), datetime.combine(today.date()+timedelta(days=1), am)]

                    query = " SELECT * FROM `golden_sample_logs` WHERE `tester_code` = %s ORDER BY `date_time_tested` DESC LIMIT 5"
                    cursor.execute(query, (tester_code,))
                    logs = cursor.fetchall()

                    if len(logs) == 0:
                        query = " SELECT `golden_sn` FROM `golden_sample_masterlist` WHERE `model` = 'Dental Monitoring' "
                        cursor.execute(query)
                        golden_sn = cursor.fetchone()
                        return 0, golden_sn['golden_sn']

                    golden_list = [row['golden_sn'] for row in logs]
                    if logs[0]['date_time_tested'] >= shift[0] and logs[0]['date_time_tested'] <= shift[1]:
                        return 1, golden_list[0]
                    else:
                        return 0, golden_list[0]
        except Exception as e:
            return -1, str(e)

    def log_golden_test(self, tester_code, serial_num, status):
        try:
            with self.connect('te') as conn:
                with conn.cursor(DictCursor) as cursor:
                    today = datetime.now()
                    query = " INSERT INTO `golden_sample_logs` VALUES (%s, %s, %s, %s, %s)"
                    cursor.execute(query, (tester_code, serial_num, self.model, today, status))
                    conn.commit()

        except Exception as e:
            print(f'Error: {e}')
            # raise e
        
        except AttributeError as e:
            return False, e

        except TypeError as e:
            return False, 'Database connection error'
    
    def get_ble_mac(self, serial_num):
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    query = "SELECT `serial_num`, `bt_mac` FROM " + f"{self.model}_progtest" + " WHERE `serial_num` = %s"
                    cursor.execute(query, (serial_num,))
                    rec = cursor.fetchall()

                    if len(rec) == 0:
                        return 0, ''
                    
                    return 1, rec[0]['bt_mac']
        except pymysql.Error as e:
            return -1, f'Error: {e}'

        except AttributeError as e:
            return -1, e

        except TypeError as e:
            return False, 'Database connection error'
        
    def prev_station_ok(self, serial_num) -> tuple[bool, str]:
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    # Check if it exists on main
                    query = " SELECT * " + f" FROM `{self.model}_main`" + " WHERE `serial_num` LIKE %s"
                    cursor.execute(query, (serial_num,))
                    status_main = cursor.fetchall()

                    if len(status_main) == 0:
                        return False, "Error: Serial not in Database"
                    
                    if not self.previous_stations:
                        for key in status_main[0].keys():
                            if key == self.station:
                                break
                            if (key != 'serial_num' and key != 'po_num'):
                                self.previous_stations.append(key)

                    fail_prev = []
                    for pst in self.previous_stations:
                        table = f"`{self.model}_{pst}`"
                        query = " SELECT `serial_num`, `status` FROM " + table + " WHERE `serial_num` LIKE %s "
                        cursor.execute(query, (serial_num,))
                        result = cursor.fetchall()
                        if len(result) == 0:
                            fail_prev.append(pst)
                        elif result[0]['status'] == 0 or status_main[0][pst] == 0:
                            fail_prev.append(pst)

                    if len(fail_prev) > 0:
                        return False, f"Error: Unit did not pass {', '.join(fail_prev)}"
                    else:
                        return True, status_main[0]['po_num']
        except TypeError as e:
            return False, 'Database connection error'
        
        except pymysql.Error as e:
            # print(f'Error: {e}')
            return False, f'Error: {e}'

        except AttributeError as e:
            return False, e
    
    def check_retest(self, serial_raw):
        serial_num = serial_raw.split('_')[0]
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:

                    # Check if serial already exists in current station
                    query = " SELECT `serial_num`, `test_rep`, `status` FROM " + self.table_name + " WHERE `serial_num` LIKE %s ORDER BY `test_rep` DESC"
                    cursor.execute(query, (f'{serial_num}%',))
                    status_retest = cursor.fetchall()
                    print(status_retest)

                    if len(status_retest) > 0 and len(status_retest) < 3:
                        if status_retest[0]['status'] == 1:
                            return True, -1
                        # Update most recent entry
                        last_test_rep = status_retest[0]['test_rep']
                        new_serial = f'{serial_num}_{last_test_rep}'
                        query = " UPDATE "+ self.table_name +" SET `serial_num` = %s WHERE `serial_num` = %s "
                        # print('DEV_MODE:', query)
                        cursor.execute(query, (new_serial, serial_num))
                        conn.commit()
                    
                    return True, len(status_retest)
        except Exception as e:
            # print(f'Error: {e}')
            return False, str(e)

        except AttributeError:
            return False, ''

        except TypeError as e:
            return False, 'Database connection error'

    def db_insert(self, head_details: dict, data: dict, tail_details: dict):
        all_values = head_details | data | tail_details
        serial_num = head_details.get('serial_num')
        final_status = tail_details.get('status')

        # data = {'lled1_intensity': 230, ... 'rled40_intensity': 175}
        
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    headers = ', '.join(all_values.keys())
                    s_set = ', '.join(['%s'] * len(all_values.keys()))
                    query = " INSERT INTO " + self.table_name + f" ({headers}) VALUES ({s_set})"
                    # print('DEV_MODE:', query)

                    readback = 0
                    for _ in range(3):
                        if readback > 0:
                            break
                        cursor.execute(query, list(all_values.values()))
                        conn.commit()

                        readback_query = "SELECT * FROM " + self.table_name + " WHERE `serial_num` = %s"
                        cursor.execute(readback_query, (serial_num,))
                        readback = len(cursor.fetchall())

                    if readback > 0:
                        # change main as well to reflect the status
                        query = f" UPDATE `{self.model}_main`" + " SET `"+ self.station +"` = %s WHERE `serial_num` = %s "
                        cursor.execute(query, (final_status, serial_num))
                        conn.commit()
                        return True, ''
                    else:
                        return False, 'Error writing to database'
        except Exception as e:
            return False, str(e)

    def log_to_csv(self, head_details: dict, data: dict, tail_details: dict):
        if not self.csv_headers:
            self.csv_headers = ['serial_num', 'po_num', 'operator_en', 'shift', 'date_time']
            tailers = ['prod_sn', 'batch_code', 'duration', 'test_rep', 'status', 'fail_reason']
            self.csv_headers.extend(data.keys())
            self.csv_headers.extend(tailers)
        
        if not os.path.exists('results.csv'):
            with open('results.csv', 'w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=self.csv_headers, extrasaction='ignore')
                writer.writeheader()

        with open('results.csv', 'a', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=self.csv_headers, extrasaction='ignore')
            if not tail_details.get('prod_sn'):
                tail_details['prod_sn'] = ''
            if not tail_details.get('batch_code'):
                tail_details['batch_code'] = ''
            final = head_details | data | tail_details
            writer.writerow(final)

# if __name__=='__main__':
#     dbconn = DatabaseConn()
#     while True:
#         serial_num = input('Enter serial:')
#         dbconn.retrieve_details('faceware', serial_num)