#!/bin/bash
cd /www/wwwroot/douyin-fire
exec /www/wwwroot/douyin-fire/venv/bin/python /www/wwwroot/douyin-fire/gui.py >> /www/wwwroot/douyin-fire/gui.log 2>&1
