"""ID 生成。"""

import uuid


def new_id() -> str:
    return str(uuid.uuid4())
