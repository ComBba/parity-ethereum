#located at /snapshot_python/app.py
from flask import Flask, render_template, request, redirect, url_for, flash
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

# Load the contract ABI
with open('ESNToken_abi.json', 'r') as abi_file:
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/account', methods=['GET', 'POST'])
def account_detail():
    address = request.args.get('address')
    if not address:
        return 'No address provided.', 400

    # Check if address is valid
    if not w3.isAddress(address):
        return 'Invalid address format.', 400

    address = w3.toChecksumAddress(address)

    # Pagination parameters
    tx_page = int(request.args.get('tx_page', 1))
    tx_per_page = 50  # Transactions per page
    tx_offset = (tx_page - 1) * tx_per_page

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

    # Total transactions count
    cursor.execute('''
        SELECT COUNT(*) as count FROM transactions
        WHERE from_address = %s OR to_address = %s
    ''', (address, address))
    result = cursor.fetchone()
    total_transactions = result['count'] if result else 0

    total_pages = (total_transactions + tx_per_page - 1) // tx_per_page

    # Fetch transactions (with pagination)
    cursor.execute('''
        SELECT * FROM transactions
        WHERE from_address = %s OR to_address = %s
        ORDER BY block_number DESC
        LIMIT %s OFFSET %s
    ''', (address, address, tx_per_page, tx_offset))
    transactions = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        'account_detail.html',
        account=account,
        transactions=transactions,
        tx_page=tx_page,
        total_pages=total_pages
    )

def claim_tokens(to_address):
    try:
        # Check if the address has already claimed tokens
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM claimed_addresses WHERE address = %s', (to_address,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return 'This address has already claimed tokens.'
        
        # Build the transaction
        nonce = w3.eth.getTransactionCount(ACCOUNT_ADDRESS)
        tx = contract.functions.transfer(to_address, w3.toWei(1000, 'ether')).buildTransaction({
            'chainId': 97,  # BSC Testnet chain ID
            'gas': 200000,
            'gasPrice': w3.toWei('10', 'gwei'),
            'nonce': nonce,
        })
        
        # Sign the transaction
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        
        # Send the transaction
        tx_hash = w3.eth.sendRawTransaction(signed_tx.rawTransaction)
        
        # Wait for the transaction receipt
        tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)
        
        # Record the claim in the database
        cursor.execute('INSERT INTO claimed_addresses (address) VALUES (%s)', (to_address,))
        conn.commit()
        cursor.close()
        conn.close()
        
        return f'Tokens have been sent! Transaction hash: {w3.toHex(tx_hash)}'
    except Exception as e:
        logging.error(f'Error sending tokens: {e}')
        return 'There was an error sending tokens.'

if __name__ == '__main__':
    app.run(debug=False)
