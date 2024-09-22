#!/bin/bash

set -e # 명령어 실행 실패 시 스크립트 중지

export FLASK_APP=app.py
export FLASK_ENV=development
flask run

#http://localhost:5000/accounts