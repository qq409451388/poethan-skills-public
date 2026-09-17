import os
import shutil
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置：config 与 secrets 在模块导入时读取环境变量。
# 测试使用临时数据目录和内存钥匙串，避免读写真实用户数据或系统 Keychain。
TEST_DATA_ROOT = Path(tempfile.gettempdir()) / "poethan-sentinel-tests"

os.environ["POETHAN_SENTINEL_TESTING"] = "1"
os.environ["POETHAN_SENTINEL_DATA_DIR"] = str(TEST_DATA_ROOT)

shutil.rmtree(TEST_DATA_ROOT, ignore_errors=True)
