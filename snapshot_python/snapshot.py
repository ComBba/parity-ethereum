import os
import json
import mysql.connector
import time
from decimal import Decimal  # Decimal 타입 사용
from web3 import Web3, IPCProvider
from tqdm import tqdm
import logging
from dotenv import load_dotenv  # 환경 변수 로드

# 로그 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 필요한 상수 선언
IPC_PATH = '/home/barahime/esn_services/chaindata/gesc.ipc'  # Parity 노드의 IPC 경로
TARGET_BLOCK = 6408977  # 스냅샷을 찍을 블록 번호
OUTPUT_JSON_FILE = 'balances.json'  # 최종 출력될 JSON 파일명
ETHERSOCIAL_JSON_FILE = '/home/barahime/esn_services/parity-ethereum/ethcore/res/ethereum/ethersocial.json'  # 초기 accounts 파일 경로
COMMIT_INTERVAL = 1000  # 데이터베이스 커밋 간격

# .env.local 파일 로드
load_dotenv()

# MySQL 연결 정보 (환경 변수에서 로드)
MYSQLDB_HOST = os.getenv('MYSQLDB_HOST')
MYSQLDB_USER = os.getenv('MYSQLDB_USER')
MYSQLDB_PASSWORD = os.getenv('MYSQLDB_PASSWORD')
MYSQLDB_DATABASE = os.getenv('MYSQLDB_DATABASE')

# MySQL 연결 정보 확인
logging.info(f"MySQL Host: {MYSQLDB_HOST}, {MYSQLDB_USER}")
def main():
    # 시작 시간 기록
    start_time = time.time()

    logging.info("Parity 노드에 연결 중...")
    # Parity 노드에 IPC를 통해 연결
    w3 = Web3(IPCProvider(IPC_PATH))
    if not w3.is_connected():
        logging.error("Parity 노드에 연결할 수 없습니다.")
        return
    logging.info("Parity 노드에 성공적으로 연결되었습니다.")

    # MySQL 데이터베이스 연결
    try:
        conn = mysql.connector.connect(
            host=MYSQLDB_HOST,
            user=MYSQLDB_USER,
            password=MYSQLDB_PASSWORD,
            database=MYSQLDB_DATABASE,
            autocommit=False  # 수동 커밋 설정
        )
        cursor = conn.cursor()
        logging.info("MySQL 데이터베이스에 성공적으로 연결되었습니다.")
    except mysql.connector.Error as err:
        logging.error(f"MySQL 연결 오류: {err}")
        return

    # 테이블 생성
    logging.info("데이터베이스 테이블 생성 중...")
    create_tables(cursor)
    # 테이블 생성 후 인덱스 생성
    create_indexes(cursor)
    # 메타데이터 테이블 생성
    create_metadata_table(cursor)
    # 이미 생성된 테이블의 balance 컬럼 데이터 타입 수정
    modify_balance_column(cursor)
    conn.commit()
    logging.info("데이터베이스 테이블이 준비되었습니다.")

    # 초기 계정 데이터 로드
    logging.info(f"'{ETHERSOCIAL_JSON_FILE}' 파일에서 초기 계정 정보 로드 중...")
    with open(ETHERSOCIAL_JSON_FILE, 'r') as f:
        genesis_data = json.load(f)
        accounts = genesis_data.get('accounts', {})

        # 초기 계정 데이터 저장
        for address, account_data in accounts.items():
            balance_str = account_data.get('balance', '0')
            if balance_str != '0':  # 잔액이 0이 아닌 경우만 저장
                try:
                    address = Web3.to_checksum_address(address)
                    balance_decimal = Decimal(balance_str)
                except Exception as e:
                    logging.error(f"유효하지 않은 주소 형식 또는 잔액 변환 오류: {address} - 오류: {e}")
                    continue
                # MySQL의 ON DUPLICATE KEY UPDATE 사용
                try:
                    cursor.execute('''
                        INSERT INTO balances (address, balance, creation_block)
                        VALUES (%s, %s, 0)
                        ON DUPLICATE KEY UPDATE balance = VALUES(balance)
                    ''', (address, str(balance_decimal)))
                except mysql.connector.Error as err:
                    logging.error(f"초기 계정 저장 오류 (주소: {address}): {err}")
        conn.commit()
    logging.info("초기 계정 정보가 데이터베이스에 저장되었습니다.")

    # 마지막으로 처리된 블록 번호 가져오기
    last_processed_block = get_last_processed_block(cursor)
    start_block = last_processed_block + 1

    # TARGET_BLOCK이 last_processed_block보다 큰지 확인
    if TARGET_BLOCK <= last_processed_block:
        logging.info(f"TARGET_BLOCK({TARGET_BLOCK})이 이미 처리된 블록({last_processed_block})보다 작거나 같습니다. 더 높은 TARGET_BLOCK을 설정하세요.")
        cursor.close()
        conn.close()
        return

    # 블록 처리 루프 시작
    logging.info(f"블록 {start_block}부터 {TARGET_BLOCK}까지 순회하여 데이터 수집을 시작합니다.")
    total_blocks = TARGET_BLOCK + 1  # 블록 번호는 0부터 시작하므로 +1
    commit_counter = 0  # 커밋 카운터 초기화

    for block_number in tqdm(range(start_block, total_blocks)):
        try:
            block = w3.eth.get_block(block_number, full_transactions=True)
        except Exception as e:
            logging.error(f"블록 {block_number}를 가져오는 중 오류 발생: {e}")
            continue

        # 트랜잭션 처리
        for tx in block.transactions:
            from_address = tx['from']
            to_address = tx['to']

            # 주소 처리 및 저장
            process_address(w3, cursor, from_address, block_number)
            if to_address:
                process_address(w3, cursor, to_address, block_number)

            # 트랜잭션 내역 저장
            try:
                value_decimal = Decimal(tx['value'])
                cursor.execute('''
                    INSERT INTO transactions (tx_hash, block_number, timestamp, from_address, to_address, value)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        block_number = VALUES(block_number),
                        timestamp = VALUES(timestamp),
                        from_address = VALUES(from_address),
                        to_address = VALUES(to_address),
                        value = VALUES(value)
                ''', (
                    tx['hash'].hex(),
                    tx['blockNumber'],
                    block.timestamp,
                    from_address,
                    to_address,
                    str(value_decimal)
                ))
            except mysql.connector.Error as err:
                logging.error(f"트랜잭션 저장 오류 (tx_hash={tx['hash'].hex()}): {err}")

            # 스마트 컨트랙트 생성 여부 확인
            if tx.get('creates'):
                contract_address = tx['creates']
                process_address(w3, cursor, contract_address, block_number, is_contract=True)

        # 트레이싱을 통한 추가 주소 수집 (엉클 보상 등 포함)
        try:
            traces = w3.manager.request_blocking('trace_block', [hex(block_number)])
            for trace in traces:
                if 'action' in trace:
                    action = trace['action']
                    if 'from' in action and action['from']:
                        process_address(w3, cursor, action['from'], block_number)
                    if 'to' in action and action['to']:
                        process_address(w3, cursor, action['to'], block_number)
                if trace.get('type') == 'reward' and 'author' in trace['action'] and trace['action']['author']:
                    process_address(w3, cursor, trace['action']['author'], block_number)
        except Exception as e:
            logging.error(f"블록 {block_number}의 트레이스를 가져오는 중 오류 발생: {e}")

        # 마지막으로 처리된 블록 업데이트
        set_last_processed_block(cursor, block_number)

        # 일정 간격으로 커밋
        commit_counter += 1
        if commit_counter >= COMMIT_INTERVAL:
            try:
                conn.commit()
                commit_counter = 0
                logging.info(f"{COMMIT_INTERVAL} 블록 처리 후 커밋 완료.")
            except mysql.connector.Error as err:
                logging.error(f"커밋 오류: {err}")
                conn.rollback()

    # 마지막 커밋
    try:
        conn.commit()
    except mysql.connector.Error as err:
        logging.error(f"최종 커밋 오류: {err}")
        conn.rollback()
    logging.info("모든 블록의 처리가 완료되었습니다.")

    # 잔액 업데이트
    logging.info("주소별 잔액 가져오기 중...")
    update_balances(w3, cursor, TARGET_BLOCK)
    try:
        conn.commit()
    except mysql.connector.Error as err:
        logging.error(f"잔액 업데이트 커밋 오류: {err}")
        conn.rollback()
    logging.info("주소별 잔액 정보가 업데이트되었습니다.")

    # 데이터베이스에서 데이터를 가져와 JSON으로 저장
    logging.info("데이터베이스에서 데이터 가져오기 및 JSON으로 저장 중...")
    try:
        cursor.execute('SELECT address, balance, creation_block FROM balances')
        rows = cursor.fetchall()
        data = [{'address': row[0], 'balance': str(row[1]), 'creation_block': row[2]} for row in rows]
    except mysql.connector.Error as err:
        logging.error(f"데이터 가져오기 오류: {err}")
        data = []

    with open(OUTPUT_JSON_FILE, 'w') as f:
        json.dump(data, f, indent=4)
    logging.info(f"모든 주소 정보가 '{OUTPUT_JSON_FILE}' 파일로 저장되었습니다.")

    # 총 소요 시간 출력
    elapsed_time = time.time() - start_time
    logging.info(f"전체 작업이 완료되었습니다. 소요 시간: {elapsed_time:.2f}초")

    # 연결 종료
    cursor.close()
    conn.close()

def create_tables(cursor):
    # balances 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS balances (
            address VARCHAR(42) PRIMARY KEY,
            balance DECIMAL(65, 0) NOT NULL DEFAULT '0',  # 데이터 타입 수정
            creation_block BIGINT NOT NULL DEFAULT 0
        )
    ''')
    # transactions 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            tx_hash VARCHAR(66) PRIMARY KEY,
            block_number BIGINT NOT NULL,
            timestamp BIGINT NOT NULL,
            from_address VARCHAR(42),
            to_address VARCHAR(42),
            value DECIMAL(65, 0)  # 데이터 타입 수정
        )
    ''')

def modify_balance_column(cursor):
    # 이미 생성된 테이블의 balance 컬럼 데이터 타입 수정
    try:
        cursor.execute('''
            ALTER TABLE balances MODIFY balance DECIMAL(65, 0) NOT NULL DEFAULT '0';
        ''')
        cursor.execute('''
            ALTER TABLE transactions MODIFY value DECIMAL(65, 0);
        ''')
        logging.info("balances 및 transactions 테이블의 balance/value 컬럼 데이터 타입이 수정되었습니다.")
    except mysql.connector.Error as err:
        logging.error(f"balance 컬럼 데이터 타입 수정 오류: {err}")

def create_indexes(cursor):
    indexes = [
        {'name': 'idx_balances_balance', 'table': 'balances', 'column': 'balance'},
        {'name': 'idx_balances_creation_block', 'table': 'balances', 'column': 'creation_block'},
        {'name': 'idx_transactions_block_number', 'table': 'transactions', 'column': 'block_number'},
        {'name': 'idx_transactions_from_address', 'table': 'transactions', 'column': 'from_address'},
        {'name': 'idx_transactions_to_address', 'table': 'transactions', 'column': 'to_address'},
    ]
    
    for index in indexes:
        index_name = index['name']
        table_name = index['table']
        column_name = index['column']
        
        # 인덱스 존재 여부 확인
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.STATISTICS 
            WHERE table_schema = DATABASE() 
              AND table_name = %s 
              AND index_name = %s
        """, (table_name, index_name))
        exists = cursor.fetchone()[0]
        
        if not exists:
            try:
                cursor.execute(f"CREATE INDEX {index_name} ON {table_name} ({column_name})")
                logging.info(f"인덱스 '{index_name}'이(가) 테이블 '{table_name}'의 컬럼 '{column_name}'에 생성되었습니다.")
            except mysql.connector.Error as err:
                logging.error(f"인덱스 생성 오류 (인덱스: {index_name}, 테이블: {table_name}): {err}")
        else:
            logging.info(f"인덱스 '{index_name}'은(는) 이미 테이블 '{table_name}'에 존재합니다.")

def create_metadata_table(cursor):
    # metadata 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metadata (
            `key` VARCHAR(50) PRIMARY KEY,
            `value` VARCHAR(255)
        )
    ''')

def get_last_processed_block(cursor):
    """
    metadata 테이블에서 마지막으로 처리된 블록 번호를 가져옵니다.
    없을 경우 -1을 반환합니다.
    """
    try:
        cursor.execute("SELECT `value` FROM metadata WHERE `key` = 'last_processed_block'")
        result = cursor.fetchone()
        if result:
            return int(result[0])
        else:
            return -1  # 초기 블록 번호 설정 (0부터 시작하기 위해)
    except mysql.connector.Error as err:
        logging.error(f"마지막 처리된 블록 가져오기 오류: {err}")
        return -1

def set_last_processed_block(cursor, block_number):
    """
    metadata 테이블에 마지막으로 처리된 블록 번호를 저장합니다.
    """
    try:
        cursor.execute("""
            INSERT INTO metadata (`key`, `value`) 
            VALUES ('last_processed_block', %s) 
            ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)
        """, (str(block_number),))
    except mysql.connector.Error as err:
        logging.error(f"마지막 처리된 블록 저장 오류: {err}")

def process_address(w3, cursor, address, current_block, is_contract=False):
    if not address:
        return
    try:
        address = Web3.to_checksum_address(address)
    except Exception as e:
        logging.error(f"유효하지 않은 주소 형식: {address} - 오류: {e}")
        return

    try:
        # 이미 존재하는 주소인지 확인
        cursor.execute('SELECT creation_block FROM balances WHERE address = %s', (address,))
        result = cursor.fetchone()

        if result is None:
            # 신규 주소이므로 생성 블록 기록
            cursor.execute('''
                INSERT INTO balances (address, balance, creation_block)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE balance = VALUES(balance)
            ''', (address, '0', current_block))
        else:
            existing_creation_block = result[0]
            if current_block < existing_creation_block:
                # 더 이전 블록에서 등장했으므로 생성 블록 업데이트
                cursor.execute('UPDATE balances SET creation_block = %s WHERE address = %s', (current_block, address))
    except mysql.connector.Error as err:
        logging.error(f"주소 처리 오류 (address={address}): {err}")

def update_balances(w3, cursor, target_block):
    try:
        cursor.execute('SELECT address FROM balances')
        addresses = cursor.fetchall()
    except mysql.connector.Error as err:
        logging.error(f"잔액 업데이트를 위한 주소 조회 오류: {err}")
        return

    for row in tqdm(addresses):
        address = row[0]
        try:
            balance = w3.eth.get_balance(address, target_block)
            # Wei 단위의 balance를 Decimal로 변환하여 저장
            balance_decimal = Decimal(balance)
            cursor.execute('UPDATE balances SET balance = %s WHERE address = %s', (str(balance_decimal), address))
        except Exception as e:
            logging.error(f"주소 {address}의 잔액을 가져오는 중 오류 발생: {e}")

if __name__ == '__main__':
    main()
