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
                    today = datetime.now()
                    am = time(7,0,0)
                    pm = time(19,0,0)
                    # Set shift start/end
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

# if __name__=='__main__':
#     dbconn = DatabaseConn()
#     while True:
#         serial_num = input('Enter serial:')
#         dbconn.retrieve_details('faceware', serial_num)