
from app import app, db
from sqlalchemy import text

with app.app_context():
    print('データベースにインデックスを追加中...')
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_store_status_timestamp ON store_status (timestamp);'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_store_status_store_name ON store_status (store_name);'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_store_status_biz_type ON store_status (biz_type);'))
    db.session.commit()
    print('インデックス追加完了')
