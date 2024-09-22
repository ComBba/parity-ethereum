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

    # 블록 가져오기
    logging.info(f"블록 {TARGET_BLOCK} 가져오기...")
    block = w3.eth.get_block(TARGET_BLOCK, full_transactions=True)
    logging.info(f"블록 {TARGET_BLOCK} 가져오기 완료.")

    # 주소 집합 초기화
    addresses = set()

    # 트랜잭션에서 주소 수집 및 거래 내역 저장
    logging.info("트랜잭션 처리 중...")
    for tx in tqdm(block.transactions):
        from_address = tx['from']
        to_address = tx['to']

        if from_address:
            addresses.add(Web3.to_checksum_address(from_address))
        if to_address:
            addresses.add(Web3.to_checksum_address(to_address))

        # 트랜잭션 내역 저장
        cursor.execute('INSERT OR REPLACE INTO transactions (tx_hash, block_number, timestamp, from_address, to_address, value) VALUES (?, ?, ?, ?, ?, ?)', (
            tx['hash'].hex(),
            tx['blockNumber'],
            block.timestamp,
            tx['from'],
            tx['to'],
            str(tx['value'])
        ))
    conn.commit()
    logging.info("트랜잭션 처리가 완료되었습니다.")

    # 트레이싱을 통한 추가 주소 수집 (엉클 보상 등 포함)
    logging.info("트레이싱을 통해 추가 주소 수집 중...")
    traces = w3.manager.request_blocking('trace_block', [hex(TARGET_BLOCK)])
    for trace in tqdm(traces):
        if 'action' in trace:
            action = trace['action']
            if 'from' in action and action['from']:
                addresses.add(Web3.to_checksum_address(action['from']))
            if 'to' in action and action['to']:
                addresses.add(Web3.to_checksum_address(action['to']))
            if trace.get('type') == 'reward' and 'author' in trace['action'] and trace['action']['author']:
                addresses.add(Web3.to_checksum_address(trace['action']['author']))
    logging.info("트레이싱을 통한 주소 수집이 완료되었습니다.")

    # 주소별 잔액 가져오기 및 데이터베이스에 저장
    logging.info("주소별 잔액 및 생성 블록 가져오기 중...")
    for address in tqdm(addresses):
        if address is None:
            continue
        try:
            balance = w3.eth.get_balance(address, TARGET_BLOCK)
        except Exception as e:
            logging.error(f"잔액을 가져오는 중 오류 발생: {e}")
            continue
        if balance > 0:
            creation_block = get_account_creation_block(w3, address, TARGET_BLOCK)
            cursor.execute('INSERT OR REPLACE INTO balances (address, balance, creation_block) VALUES (?, ?, ?)', (address, str(balance), creation_block))
    conn.commit()
    logging.info("주소별 잔액 및 생성 블록 정보가 저장되었습니다.")

    # 데이터베이스에서 데이터를 가져와 JSON으로 저장
    logging.info("데이터베이스에서 데이터 가져오기 및 JSON으로 저장 중...")
    cursor.execute('SELECT address, balance, creation_block FROM balances')
    data = [{'address': row[0], 'balance': row[1], 'creation_block': row[2]} for row in cursor.fetchall()]

    with open(OUTPUT_JSON_FILE, 'w') as f:
        json.dump(data, f, indent=4)
    logging.info(f"잔액 데이터가 '{OUTPUT_JSON_FILE}' 파일로 저장되었습니다.")

    # 총 소요 시간 출력
    elapsed_time = time.time() - start_time
    logging.info(f"전체 작업이 완료되었습니다. 소요 시간: {elapsed_time:.2f}초")

    # 연결 종료
    conn.close()

def get_account_creation_block(w3, address, target_block):
    # 계정의 생성 블록 번호를 찾기 위한 함수
    # 여기서는 간단히 블록 0부터 target_block까지 이분 탐색을 통해 찾습니다.

    if w3.eth.get_code(address, block_identifier=target_block) != b'':
        # 스마트 컨트랙트인 경우
        is_contract = True
    else:
        # 외부 소유 계정인 경우
        is_contract = False

    left = 0
    right = target_block
    creation_block = target_block

    while left <= right:
        mid = (left + right) // 2
        code = w3.eth.get_code(address, block_identifier=mid)
        balance = w3.eth.get_balance(address, block_identifier=mid)

        if is_contract and code != b'':
            creation_block = mid
            right = mid - 1
        elif not is_contract and balance > 0:
            creation_block = mid
            right = mid - 1
        else:
            left = mid + 1

    return creation_block

if __name__ == '__main__':
    main()
