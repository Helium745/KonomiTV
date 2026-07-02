
# RecordingPresetsRouter は EDCB 専用の録画プリセット機能だったため廃止
# mirakc 移行後は不要なため、空ルーターとして残す

from fastapi import APIRouter


# ルーター (エンドポイントなし)
router = APIRouter(
    tags = ['Recording Presets'],
    prefix = '/api/recording',
)
