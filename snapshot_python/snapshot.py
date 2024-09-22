import json
import sqlite3
import time
from web3 import Web3, IPCProvider
from tqdm import tqdm
import logging

# 로그 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 필요한 상수 선언
IPC_PATH = '/home/barahime/esn_services/chaindata/gesc.ipc'  # Parity 노드의 IPC 경로
TARGET_BLOCK = 6000000  # 스냅샷을 찍을 블록 번호
DATABASE_FILE = 'balances.db'  # SQLite 데이터베이스 파일명
OUTPUT_JSON_FILE = 'balances.json'  # 최종 출력될 JSON 파일명
ETHERSOCIAL_JSON_FILE = '/home/barahime/esn_services/parity-ethereum/ethcore/res/ethereum/ethersocial.json'  # 초기 accounts 파일 경로
COMMIT_INTERVAL = 1000  # 데이터베이스 커밋 간격

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

    # SQLite 데이터베이스 연결
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    # 테이블 생성
    logging.info("데이터베이스 테이블 생성 중...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS balances (
            address TEXT PRIMARY KEY,
            balance TEXT,
            creation_block INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            tx_hash TEXT PRIMARY KEY,
            block_number INTEGER,
            timestamp INTEGER,
            from_address TEXT,
            to_address TEXT,
            value TEXT
        )
    ''')
    # 테이블 생성 후 인덱스 생성
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_balances_balance ON balances (balance)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_balances_creation_block ON balances (creation_block)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_block_number ON transactions (block_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_from_address ON transactions (from_address)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_to_address ON transactions (to_address)')
    conn.commit()
    logging.info("데이터베이스 테이블이 준비되었습니다.")

    # 초기 계정 데이터 로드
    logging.info(f"'{ETHERSOCIAL_JSON_FILE}' 파일에서 초기 계정 정보 로드 중...")
    with open(ETHERSOCIAL_JSON_FILE, 'r') as f:
        genesis_data = json.load(f)
        accounts = genesis_data.get('accounts', {})

        # 초기 계정 데이터 저장
        for address, account_data in accounts.items():
            balance = account_data.get('balance', '0')
            if balance != '0':  # 잔액이 0이 아닌 경우만 저장
                cursor.execute('INSERT OR REPLACE INTO balances (address, balance, creation_block) VALUES (?, ?, 0)', 
                               (Web3.to_checksum_address(address), balance))
        conn.commit()
    logging.info("초기 계정 정보가 데이터베이스에 저장되었습니다.")

    # 블록 0부터 TARGET_BLOCK까지 순회
    logging.info(f"블록 0부터 {TARGET_BLOCK}까지 순회하여 데이터 수집을 시작합니다.")
    total_blocks = TARGET_BLOCK + 1  # 블록 번호는 0부터 시작하므로 +1
    commit_counter = 0  # 커밋 카운터 초기화

    for block_number in tqdm(range(0, total_blocks)):
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
            cursor.execute('INSERT OR IGNORE INTO transactions (tx_hash, block_number, timestamp, from_address, to_address, value) VALUES (?, ?, ?, ?, ?, ?)', (
                tx['hash'].hex(),
                tx['blockNumber'],
                block.timestamp,
                from_address,
                to_address,
                str(tx['value'])
            ))

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

        # 일정 간격으로 커밋
        commit_counter += 1
        if commit_counter >= COMMIT_INTERVAL:
            conn.commit()
            commit_counter = 0

    # 마지막 커밋
    conn.commit()
    logging.info("모든 블록의 처리가 완료되었습니다.")

    # 잔액 업데이트
    logging.info("주소별 잔액 가져오기 중...")
    update_balances(w3, cursor, TARGET_BLOCK)
    conn.commit()
    logging.info("주소별 잔액 정보가 업데이트되었습니다.")

    # 데이터베이스에서 데이터를 가져와 JSON으로 저장
    logging.info("데이터베이스에서 데이터 가져오기 및 JSON으로 저장 중...")
    cursor.execute('SELECT address, balance, creation_block FROM balances')
    data = [{'address': row[0], 'balance': row[1], 'creation_block': row[2]} for row in cursor.fetchall()]

    with open(OUTPUT_JSON_FILE, 'w') as f:
        json.dump(data, f, indent=4)
    logging.info(f"모든 주소 정보가 '{OUTPUT_JSON_FILE}' 파일로 저장되었습니다.")

    # 총 소요 시간 출력
    elapsed_time = time.time() - start_time
    logging.info(f"전체 작업이 완료되었습니다. 소요 시간: {elapsed_time:.2f}초")

    # 연결 종료
    conn.close()

def process_address(w3, cursor, address, current_block, is_contract=False):
    if not address:
        return
    address = Web3.to_checksum_address(address)

    # 이미 존재하는 주소인지 확인
    cursor.execute('SELECT creation_block FROM balances WHERE address = ?', (address,))
    result = cursor.fetchone()

    if result is None:
        # 신규 주소이므로 생성 블록 기록
        cursor.execute('INSERT INTO balances (address, balance, creation_block) VALUES (?, ?, ?)', (address, '0', current_block))
    else:
        existing_creation_block = result[0]
        if current_block < existing_creation_block:
            # 더 이전 블록에서 등장했으므로 생성 블록 업데이트
            cursor.execute('UPDATE balances SET creation_block = ? WHERE address = ?', (current_block, address))

def update_balances(w3, cursor, target_block):
    cursor.execute('SELECT address FROM balances')
    addresses = cursor.fetchall()

    for row in tqdm(addresses):
        address = row[0]
        try:
            balance = w3.eth.get_balance(address, target_block)
            cursor.execute('UPDATE balances SET balance = ? WHERE address = ?', (str(balance), address))
        except Exception as e:
            logging.error(f"주소 {address}의 잔액을 가져오는 중 오류 발생: {e}")

if __name__ == '__main__':
    main()
