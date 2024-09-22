from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import math

app = Flask(__name__)
DATABASE_FILE = 'balances.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    target_block = 6000000  # 스냅샷 블록 번호 (필요에 따라 변수로 관리)
    return render_template('index.html', target_block=target_block)

@app.route('/accounts')
def accounts():
    sort = request.args.get('sort', 'balance')  # 정렬 기준: balance 또는 creation_block
    order = request.args.get('order', 'desc')  # 정렬 순서: asc 또는 desc
    page = int(request.args.get('page', 1))  # 현재 페이지 번호
    per_page = 10  # 페이지당 항목 수

    conn = get_db_connection()
    cursor = conn.cursor()

    # 전체 계정 수 가져오기
    cursor.execute('SELECT COUNT(*) FROM balances')
    total_accounts = cursor.fetchone()[0]
    total_pages = math.ceil(total_accounts / per_page)

    # 정렬 및 페이징 적용하여 계정 목록 가져오기
    offset = (page - 1) * per_page
    cursor.execute(f'''
        SELECT address, balance, creation_block
        FROM balances
        ORDER BY {sort} {order}
        LIMIT ? OFFSET ?
    ''', (per_page, offset))
    accounts = cursor.fetchall()
    conn.close()

    return render_template('accounts.html', accounts=accounts, page=page, total_pages=total_pages, sort=sort, order=order)

@app.route('/account', methods=['GET'])
def account_detail():
    address = request.args.get('address')
    if not address:
        return '계정 주소를 입력해 주세요.', 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # 계정 정보 가져오기
    cursor.execute('SELECT * FROM balances WHERE address = ?', (address,))
    account = cursor.fetchone()

    if account is None:
        conn.close()
        return '계정을 찾을 수 없습니다.', 404

    # 해당 계정의 거래 내역 가져오기
    cursor.execute('''
        SELECT * FROM transactions
        WHERE from_address = ? OR to_address = ?
        ORDER BY block_number DESC
    ''', (address, address))
    transactions = cursor.fetchall()
    conn.close()

    return render_template('account_detail.html', account=account, transactions=transactions)

