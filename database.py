import os
import pymysql
from pymysql.cursors import DictCursor
from datetime import datetime, time, timedelta

DB_USER = os.getenv('DB_USER', 'labeling')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'labeling')

class DatabaseConn:
    def __init__(self, input_config: dict):
        self.config = {'user': DB_USER,
                        'password': DB_PASSWORD,
                        'autocommit': False,
                        'use_unicode': True,
                        'charset': 'utf8mb4'
                        }
        self.config['host'] = input_config['host']

        self.schema = input_config['customer']
        self.model = input_config['model']
        self.station = input_config['station']
        self.main_table = f"`{self.model}_main`"
        self.table_name = f"`{self.model}_{self.station}`"

        self.previous_stations = []

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
                    # Check masterlist if GS exists
                    master_query = " SELECT `golden_sn` FROM `golden_sample_masterlist` WHERE `tester_code` = %s "
                    cursor.execute(master_query, (tester_code,))
                    golden_sn = cursor.fetchone()
                    if golden_sn is None:
                        # No registered golden sample yet
                        return 1, ''

                    today = datetime.now()
                    am = time(7,0,0)
                    pm = time(19,0,0)
                    # Set shift start/end
                    if today.time() < am:
                        shift = [datetime.combine(today.date()-timedelta(days=1), pm), datetime.combine(today.date(), am)]
                    elif today.time() >= pm:
                        shift = [datetime.combine(today.date(), pm), datetime.combine(today.date()+timedelta(days=1), am)]
                    else:
                        shift = [datetime.combine(today.date(), am), datetime.combine(today.date(), pm)]

                    # Check if GS has been logged for the given shift
                    query = " SELECT * FROM `golden_sample_logs` WHERE `tester_code` = %s AND `date_time` BETWEEN %s AND %s"
                    cursor.execute(query, (tester_code, shift[0], shift[1]))
                    logs = cursor.fetchone()
                    # print(logs)

                    if logs is None:
                        return 0, golden_sn['golden_sn']
                    else:
                        return 1, golden_sn['golden_sn']

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
            # print(f'Error: {e}')
            raise e
    
    def get_ble_mac(self, serial_num):
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    query = "SELECT `serial_num`, `bt_mac` FROM " + f"{self.model}_progtest" + " WHERE `serial_num` = %s"
                    cursor.execute(query, (serial_num,))
                    rec = cursor.fetchone()

                    if not rec:
                        return {'error': 'No MAC Address in Database'}
                    
                    return {'bt_mac': rec['bt_mac']}
        except Exception as e:
            return {'error': str(e)}
    
    def get_db_status(self, serial_num):
        try:
            new_db = self.connect(self.schema)
            if new_db is None:
                return {'error': 'Database connection error'}
            
            with new_db as dbconn:
                with dbconn.cursor(DictCursor) as cursor:
                    # Get PO number and station status from main
                    main_query = " SELECT * FROM " + self.main_table + " WHERE `serial_num` = %s "
                    cursor.execute(main_query, (serial_num,))
                    main_status = cursor.fetchone()

                    if not main_status:
                        return {'error': "Serial Number not in Database"}

                    ponum = main_status['po_num']

                    curr_test_rep = 0
                    failed_stations = []
                    for column, col_status in main_status.items():
                        if column == 'serial_num' or column == 'po_num':
                            continue
                        elif column == self.station:
                            # Get current test_rep
                            retest_query = " SELECT `status`, `test_rep` FROM " + self.table_name + " WHERE `serial_num` LIKE %s ORDER BY `test_rep` DESC"
                            cursor.execute(retest_query, (f'{serial_num}%',))
                            response = cursor.fetchall()
                            curr_test_rep = len(response) + 1
                            if len(response) > 0 and response[0]['status'] == 1:
                                return {'error': "Already passed", 'po_num': ponum, 'test_rep': curr_test_rep}
                            break
                        else:
                            # Check if unit did not pass a previous station
                            table_name = f"`{self.model}_{column}`"
                            sub_query = " SELECT `serial_num`, `status` FROM " + table_name + " WHERE `serial_num` = %s "
                            cursor.execute(sub_query, (serial_num,))
                            sub_status = cursor.fetchone()
                            # Double-check with status in Main
                            if sub_status['status'] == 0 or col_status == 0:
                                failed_stations.append(column)

                    if len(failed_stations) > 0:
                        return {'error': "Unit did not pass " + ', '.join(failed_stations)}

                    if curr_test_rep > 3 and curr_test_rep % 3 == 1:
                        fa_query = " SELECT `serial_num`, `farepair_status` FROM `fa`.`main` WHERE `serial_num` = %s"
                        cursor.execute(fa_query, (serial_num,))
                        resp = cursor.fetchone()
                        if not resp or resp['farepair_status'] != 4:
                            return {'error': "No more retest allowed until FA"}
                        else:
                            return {'po_num': ponum, 'test_rep': curr_test_rep, 'need_pass': True}
                    # else:
                    return {'po_num': ponum, 'test_rep': curr_test_rep}

        except Exception as e:
            return {'error': str(e)}
            # raise e

    def update_last_entry(self, cursor: DictCursor, serial_num: str):
        get_query = " SELECT `serial_num`, `test_rep` FROM " + self.table_name + " WHERE `serial_num` LIKE %s ORDER BY `test_rep` DESC"
        cursor.execute(get_query, (f"{serial_num}%",))
        last_rep = cursor.fetchone()['test_rep']

        new_serial = f"{serial_num}_{last_rep}"
        set_query = " UPDATE "+ self.table_name +" SET `serial_num` = %s WHERE `serial_num` = %s "
        cursor.execute(set_query, (new_serial, serial_num))

    def db_insert(self, head_details: dict, data: dict, tail_details: dict):
        all_values = head_details | data | tail_details
        serial_num = head_details.get('serial_num')
        final_status = tail_details.get('status')

        # data = {'lled1_intensity': 230, ... 'rled40_intensity': 175}
        
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    if tail_details.get('test_rep', 1) > 1:
                        self.update_last_entry(cursor, serial_num)

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
            # return False, str(e)
            raise e

class GoldenSample:
    def __init__(self, model, tester_code):
        self.config = {'host': '192.168.20.210',
                        'user': DB_USER,
                        'password': DB_PASSWORD,
                        'database': 'te',
                        'autocommit': False,
                        'use_unicode': True,
                        'charset': 'utf8mb4'
                        }
        self.tester_code = tester_code
        self.model = model
        self.golden_serial = ''

        self._tested_for_curr_shift = False
        self._current_shift = ''
        self.set_shift()

    def connect(self):
        try:
            return pymysql.connect(**self.config)
        except pymysql.Error as e:
            raise Exception(f"Database error: {e}")

    def set_shift(self):
        today = datetime.now()

        # shift turnover = reset state
        # otherwise, maintain current state
        if today.time() >= time(7,0,0) and today.time() < time(19,0,0):
            if self._current_shift != 'A':
                self._current_shift = 'A'
                self._tested_for_curr_shift = False
        else:
            if self._current_shift != 'B':
                self._current_shift = 'B'
                self._tested_for_curr_shift = False

    def get_current_shift(self):
        today = datetime.now()
        am = time(7,0,0)
        pm = time(19,0,0)
        # Set shift start/end
        if today.time() < am:
            # Before 7AM, shift B started yesterday
            shift = [datetime.combine(today.date()-timedelta(days=1), pm), datetime.combine(today.date(), am)]
        elif today.time() >= pm:
            # After 7PM, shift B ends tomorrow
            shift = [datetime.combine(today.date(), pm), datetime.combine(today.date()+timedelta(days=1), am)]
        else:
            # shift A from 7AM to 7PM
            shift = [datetime.combine(today.date(), am), datetime.combine(today.date(), pm)]

        return shift

    def _check_logs(self):
        with self.connect() as dbconn:
            with dbconn.cursor(DictCursor) as cursor:
                master_query = " SELECT `golden_sn` FROM `golden_sample_masterlist` WHERE `tester_code` = %s "
                cursor.execute(master_query, (self.tester_code,))
                golden_sn = cursor.fetchone()
                if golden_sn is None:
                    # No registered golden sample yet
                    return True

                if self.golden_serial == '':
                    self.golden_serial = golden_sn['golden_sn']

                shift = self.get_current_shift()

                # Check if GS has been logged for the given shift
                query = " SELECT * FROM `golden_sample_logs` WHERE `tester_code` = %s AND `date_time_tested` BETWEEN %s AND %s"
                cursor.execute(query, (self.tester_code, shift[0], shift[1]))
                logs = cursor.fetchone()
                print(logs)
                print(self.golden_serial)

                if logs is None:
                    return False
                else:
                    self._tested_for_curr_shift = True
                    return True

    def logged(self):
        self.set_shift()

        if self._tested_for_curr_shift:
            return True
        else:
            return self._check_logs()

    def serial(self):
        return self.golden_serial

    def log_test(self, status):
        with pymysql.connect(**self.config) as dbconn:
            with dbconn.cursor(DictCursor) as cursor:
                today = datetime.now()
                query = " INSERT INTO `golden_sample_logs` VALUES (%s, %s, %s, %s, %s)"
                cursor.execute(query, (self.tester_code, self.golden_serial, self.model, today, status))
                dbconn.commit()
        
        self._tested_for_curr_shift = True

# if __name__=='__main__':
#     dbconn = DatabaseConn()
#     while True:
#         serial_num = input('Enter serial:')
#         dbconn.retrieve_details('faceware', serial_num)