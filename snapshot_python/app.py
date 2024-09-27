#located at /snapshot_python/app.py
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, render_template_string
import mysql.connector
from decimal import Decimal
import logging
import os
from dotenv import load_dotenv
from datetime import datetime
from web3 import Web3, HTTPProvider
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with your secret key

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# MySQL connection information
MYSQLDB_HOST = os.getenv('MYSQLDB_HOST')
MYSQLDB_USER = os.getenv('MYSQLDB_USER')
MYSQLDB_PASSWORD = os.getenv('MYSQLDB_PASSWORD')
MYSQLDB_DATABASE = os.getenv('MYSQLDB_DATABASE')

# Initialize Web3
BSC_RPC_URL = os.getenv('BSC_RPC_URL')
w3 = Web3(HTTPProvider(BSC_RPC_URL))

# Load contract and account information
PRIVATE_KEY = os.getenv('PRIVATE_KEY')
ACCOUNT_ADDRESS = os.getenv('ACCOUNT_ADDRESS')
CONTRACT_ADDRESS = os.getenv('CONTRACT_ADDRESS')

# Ensure addresses are in checksum format
ACCOUNT_ADDRESS = Web3.to_checksum_address(ACCOUNT_ADDRESS)
CONTRACT_ADDRESS = Web3.to_checksum_address(CONTRACT_ADDRESS)

# Load the contract ABI
with open('./ESN/2.ESNToken_abi.json', 'r') as abi_file:
    contract_abi = json.load(abi_file)

# Create the contract instance
contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=contract_abi)

def get_db_connection():
    """
    Create a MySQL database connection.
    """
    conn = mysql.connector.connect(
        host=MYSQLDB_HOST,
        user=MYSQLDB_USER,
        password=MYSQLDB_PASSWORD,
        database=MYSQLDB_DATABASE,
        autocommit=True  # Auto-commit mode
    )
    return conn

def format_esn_balance(balance_str):
    """
    Convert raw balance string (in Wei) to ESN units and format it.
    """
    try:
        # Convert to decimal with 18 decimal places
        balance_decimal = Decimal(balance_str) / Decimal('1000000000000000000')
        # Keep 18 decimal places and add commas
        formatted_balance = "{:,.18f} ESN".format(balance_decimal)
        return formatted_balance
    except Exception as e:
        logging.error(f"Balance formatting error: {e}")
        return balance_str  # Return original string on error

# Register custom filters
app.jinja_env.filters['format_esn'] = format_esn_balance

def format_datetime(value):
    """
    Convert Unix timestamp to 'YYYY-MM-DD HH:MM:SS' format.
    """
    try:
        dt = datetime.fromtimestamp(value)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logging.error(f"Timestamp formatting error: {e}")
        return value  # Return original value on error

app.jinja_env.filters['format_datetime'] = format_datetime

def create_claimed_addresses_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Check if the table exists
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS claimed_addresses (
            address VARCHAR(42) PRIMARY KEY,
            amount DECIMAL(38, 18) NOT NULL
        );
    ''')
    conn.commit()
    cursor.close()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/accounts')
def accounts():
    # 페이지, 정렬 등의 파라미터 처리
    sort = request.args.get('sort', 'balance')
    order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = 50  # 한 페이지당 표시할 계정 수

    valid_sort_columns = ['address', 'balance', 'creation_block']
    if sort not in valid_sort_columns:
        sort = 'balance'

    if order not in ['asc', 'desc']:
        order = 'desc'

    offset = (page - 1) * per_page

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 총 계정 수 가져오기
        cursor.execute('SELECT COUNT(*) as count FROM balances')
        result = cursor.fetchone()
        total_accounts = result['count'] if result else 0

        # 계정 목록 가져오기
        sort_column = sort  # 이미 유효성 검사를 했으므로 안전

        query = f'''
            SELECT address, balance, creation_block
            FROM balances
            ORDER BY {sort_column} {order}
            LIMIT %s OFFSET %s
        '''
        cursor.execute(query, (per_page, offset))
        accounts = cursor.fetchall()
    except mysql.connector.Error as e:
        logging.error(f"데이터베이스 접근 오류: {e}")
        accounts = []
        total_accounts = 0
    finally:
        cursor.close()
        conn.close()

    total_pages = (total_accounts + per_page - 1) // per_page

    return render_template('accounts.html', accounts=accounts, page=page, total_pages=total_pages, sort=sort, order=order)

@app.route('/account', methods=['GET', 'POST'])
def account_detail():
    address = request.args.get('address')
    if not address:
        return 'No address provided.', 400

    # Check if address is valid
    if not Web3.is_address(address):
        return 'Invalid address format.', 400

    address = Web3.to_checksum_address(address)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('SELECT * FROM balances WHERE address = %s', (address,))
    account = cursor.fetchone()

    if account is None:
        cursor.close()
        conn.close()
        return 'Account not found.', 404

    if request.method == 'POST':
        # Handle token claim
        message = claim_tokens(address)
        flash(message)
        return redirect(url_for('account_detail', address=address))
    
    # Check if the address has already claimed tokens and get the amount
    cursor.execute('SELECT amount FROM claimed_addresses WHERE address = %s', (address,))
    claimed_data = cursor.fetchone()
    claimed_amount = claimed_data['amount'] if claimed_data else None

    cursor.close()
    conn.close()

    return render_template(
        'account_detail.html',
        account=account,
        claimed_amount=claimed_amount  # 새로운 변수 전달
    )

@app.route('/get_transactions')
def get_transactions():
    address = request.args.get('address')
    page = int(request.args.get('page', 1))
    tx_per_page = 50
    tx_offset = (page - 1) * tx_per_page

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Total transactions count
    cursor.execute('''
        SELECT COUNT(*) as count FROM transactions
        WHERE from_address = %s OR to_address = %s
    ''', (address, address))
    result = cursor.fetchone()
    total_transactions = result['count'] if result else 0
    total_pages = (total_transactions + tx_per_page - 1) // tx_per_page

    # Fetch transactions
    cursor.execute('''
        SELECT block_number, timestamp, from_address, to_address, value, tx_hash FROM transactions
        WHERE from_address = %s OR to_address = %s
        ORDER BY block_number DESC
        LIMIT %s OFFSET %s
    ''', (address, address, tx_per_page, tx_offset))
    transactions = cursor.fetchall()

    cursor.close()
    conn.close()

    # 트랜잭션 테이블 HTML 렌더링
    transactions_html = render_template('transactions_table.html', transactions=transactions)
    # 페이지네이션 HTML 렌더링
    pagination_html = render_template('pagination.html', total_pages=total_pages, current_page=page, address=address)

    return jsonify({
        'transactions_html': transactions_html,
        'pagination_html': pagination_html
    })

def claim_tokens(to_address):
    try:
        # Check if the address has already claimed tokens
        conn = get_db_connection()
        cursor = conn.cursor()

        # 이미 청구했는지 확인
        cursor.execute('SELECT * FROM claimed_addresses WHERE address = %s', (to_address,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return 'This address has already claimed tokens.'

        # balances 테이블에서 잔액 가져오기
        cursor.execute('SELECT balance FROM balances WHERE address = %s', (to_address,))
        balance_data = cursor.fetchone()
        if not balance_data:
            cursor.close()
            conn.close()
            return 'No balance found for this address.'

        # 잔액을 Wei 단위의 정수로 변환
        balance_str = balance_data[0]  # balance는 문자열로 저장되어 있음
        amount_to_send = int(balance_str)

        # 잔액이 0보다 큰지 확인
        if amount_to_send <= 0:
            cursor.close()
            conn.close()
            return 'The balance for this address is zero.'

        # Build the transaction
        nonce = w3.eth.get_transaction_count(ACCOUNT_ADDRESS)
        tx = contract.functions.transfer(to_address, amount_to_send).build_transaction({
            'chainId': 97,  # BSC Testnet chain ID
            'gas': 200000,
            'gasPrice': w3.to_wei('10', 'gwei'),
            'nonce': nonce,
        })

        # Sign the transaction
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)

        # Send the transaction
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        # Wait for the transaction receipt
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        # Convert the amount back to ESN units for storage
        amount_esn = Decimal(amount_to_send) / Decimal('1000000000000000000')

        # Record the claim in the database with the amount
        cursor.execute('INSERT INTO claimed_addresses (address, amount) VALUES (%s, %s)', (to_address, amount_esn))
        conn.commit()
        cursor.close()
        conn.close()

        return f'Tokens have been sent! Transaction hash: {w3.to_hex(tx_hash)}'
    except Exception as e:
        logging.error(f'Error sending tokens: {e}')
        return 'There was an error sending tokens.'

if __name__ == '__main__':
    create_claimed_addresses_table()
    app.run(debug=False)