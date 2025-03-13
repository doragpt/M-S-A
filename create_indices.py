#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sqlite3
from database import get_db_connection
import sys

"""
パフォーマンス改善のためのインデックス作成スクリプト
"""

def create_indices():
    try:
        conn = get_db_connection()

        # store_nameインデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_store_name ON history(store_name)')
        print("- store_name インデックスを作成しました")

        # timestampインデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON history(timestamp)')
        print("- timestamp インデックスを作成しました")

        # biz_typeインデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_biz_type ON history(biz_type)')
        print("- biz_type インデックスを作成しました")

        # areaインデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_area ON history(area)')
        print("- area インデックスを作成しました")

        # genreインデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_genre ON history(genre)')
        print("- genre インデックスを作成しました")

        # 複合インデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_store_time ON history(store_name, timestamp)')
        print("- store_name + timestamp 複合インデックスを作成しました")

        # 集計用インデックス
        conn.execute('CREATE INDEX IF NOT EXISTS idx_working_active ON history(working_staff, active_staff)')
        print("- working_staff + active_staff 集計用インデックスを作成しました")

        conn.commit()
        conn.close()

        print("すべてのインデックスを作成しました。クエリのパフォーマンスが向上します。")
        return True
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return False

if __name__ == "__main__":
    success = create_indices()
    sys.exit(0 if success else 1)