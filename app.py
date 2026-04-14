
import os, re
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "korea_lighting_2026")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
PRODUCT_FILES_TABLE = os.getenv("SUPABASE_PRODUCT_FILES_TABLE", "product_files")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "product-files")

FILE_TYPE_KEYWORDS = {
    "datasheet": ["사양서", "datasheet", "data sheet"],
    "reliability": ["신뢰성", "reliability"],
    "certificate": ["인증서", "certificate", "certification", "ul", "kc"],
    "lm80": ["lm80", "lm-80"],
    "tm21": ["tm21", "tm-21"],
    "report": ["report", "리포트", "시험성적서", "test report"],
    "application_note": ["application note", "app note", "적용자료"],
    "catalog": ["catalog", "카탈로그"],
}
COMPARE_SPEC_KEYS = [
    ("part_number", "품번"),
    ("manufacturer", "제조사"),
    ("package", "패키지"),
    ("cct_k", "CCT(K)"),
    ("cri", "CRI"),
    ("luminous_flux_lm", "광속(lm)"),
    ("efficacy_lm_w", "효율(lm/W)"),
    ("forward_voltage_typ_v", "Vf Typ(V)"),
    ("test_current_ma", "시험전류(mA)"),
]

app = FastAPI(title="File-first Product Chatbot", version="5.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str

class CompareRequest(BaseModel):
    part_numbers: List[str]

def get_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=500, detail="SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def check_admin_token(token: Optional[str]):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="관리자 토큰이 올바르지 않습니다.")

def table_select_all(table_name: str) -> List[Dict[str, Any]]:
    sb = get_client()
    result = sb.table(table_name).select("*").execute()
    return result.data or []

def list_product_files(part_number: Optional[str] = None) -> List[Dict[str, Any]]:
    sb = get_client()
    q = sb.table(PRODUCT_FILES_TABLE).select("*")
    if part_number:
        q = q.eq("part_number", part_number)
    result = q.execute()
    return result.data or []

def insert_product_file(record: Dict[str, Any]):
    sb = get_client()
    sb.table(PRODUCT_FILES_TABLE).insert(record).execute()

def get_product_file(file_id: str) -> Dict[str, Any]:
    sb = get_client()
    result = sb.table(PRODUCT_FILES_TABLE).select("*").eq("id", file_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="파일 정보를 찾지 못했습니다.")
    return result.data[0]

def delete_product_file(file_id: str):
    sb = get_client()
    row = get_product_file(file_id)
    storage_path = row.get("storage_path")
    if storage_path:
        try:
            sb.storage.from_(STORAGE_BUCKET).remove([storage_path])
        except Exception:
            pass
    sb.table(PRODUCT_FILES_TABLE).delete().eq("id", file_id).execute()

def upload_file_to_storage(file_bytes: bytes, original_filename: str, part_number: str, file_type: str) -> str:
    sb = get_client()
    safe_part = re.sub(r"[^A-Za-z0-9\-_]", "_", part_number or "unknown")
    safe_name = re.sub(r"[^A-Za-z0-9\-_.]", "_", original_filename)
    path = f"{safe_part}/{file_type}/{safe_name}"
    sb.storage.from_(STORAGE_BUCKET).upload(path, file_bytes, {"content-type": "application/pdf", "upsert": "true"})
    return path

def get_signed_download_url(storage_path: str, expires_in: int = 3600) -> str:
    sb = get_client()
    result = sb.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, expires_in)
    if isinstance(result, dict):
        return result.get("signedURL") or result.get("signed_url") or ""
    return ""

def normalize_part_number(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def detect_requested_file_type(query: str) -> Optional[str]:
    q = query.lower()
    for file_type, keywords in FILE_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in q:
                return file_type
    return None

def infer_part_number_from_query(query: str) -> str:
    q = re.sub(r"(사양서|다운로드|자료|파일|비교|추천|신뢰성|인증서|lm80|tm21|datasheet|reliability|certificate|report)", " ", query, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()

def grouped_parts() -> Dict[str, Dict[str, Any]]:
    files = table_select_all(PRODUCT_FILES_TABLE)
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in files:
        pn = str(row.get("part_number") or "").strip()
        if not pn:
            continue
        key = normalize_part_number(pn)
        if key not in grouped:
            grouped[key] = {
                "part_number": pn,
                "manufacturer": row.get("manufacturer"),
                "package": row.get("package"),
                "cct_k": row.get("cct_k"),
                "cri": row.get("cri"),
                "luminous_flux_lm": row.get("luminous_flux_lm"),
                "efficacy_lm_w": row.get("efficacy_lm_w"),
                "forward_voltage_typ_v": row.get("forward_voltage_typ_v"),
                "test_current_ma": row.get("test_current_ma"),
                "files": [],
            }
        grouped[key]["files"].append(row)
        for f in ["manufacturer","package","cct_k","cri","luminous_flux_lm","efficacy_lm_w","forward_voltage_typ_v","test_current_ma"]:
            if grouped[key].get(f) in [None, "", 0] and row.get(f) not in [None, ""]:
                grouped[key][f] = row.get(f)
    return grouped

def match_part(query: str) -> List[Dict[str, Any]]:
    groups = grouped_parts()
    nq = normalize_part_number(infer_part_number_from_query(query))
    if not nq:
        return list(groups.values())[:10]
    exact = [v for k, v in groups.items() if k == nq]
    if exact:
        return exact
    contains = [v for k, v in groups.items() if nq in k or k in nq]
    if contains:
        return contains[:10]
    return []

def best_matching_file(part_row: Dict[str, Any], requested_type: Optional[str]) -> Optional[Dict[str, Any]]:
    files = part_row.get("files", [])
    if not files:
        return None
    if requested_type:
        typed = [f for f in files if str(f.get("file_type","")) == requested_type]
        if typed:
            return typed[0]
    datasheets = [f for f in files if str(f.get("file_type","")) == "datasheet"]
    return datasheets[0] if datasheets else files[0]

def summarize_specs(part_row: Dict[str, Any]) -> str:
    parts = []
    for label, key in [("패키지","package"),("CCT","cct_k"),("CRI","cri"),("광속","luminous_flux_lm"),("효율","efficacy_lm_w")]:
        v = part_row.get(key)
        if v not in [None, ""]:
            suffix = "K" if key == "cct_k" else ("lm" if key == "luminous_flux_lm" else ("lm/W" if key=="efficacy_lm_w" else ""))
            parts.append(f"{label} {v}{suffix}")
    return ", ".join(parts) if parts else "등록된 스펙 요약 없음"

def compare_parts(part_numbers: List[str]) -> Dict[str, Any]:
    rows = []
    for pn in part_numbers:
        matched = match_part(pn)
        if not matched:
            raise HTTPException(status_code=404, detail=f"품번을 찾지 못했습니다: {pn}")
        rows.append(matched[0])
    a, b = rows[0], rows[1]
    table = []
    for key, label in COMPARE_SPEC_KEYS:
        table.append({"항목": label, a["part_number"]: a.get(key), b["part_number"]: b.get(key)})
    for row in rows:
        ds = best_matching_file(row, "datasheet")
        row["datasheet_download_url"] = f"/files/{ds['id']}" if ds else None
        row["datasheet_file_name"] = ds.get("file_name") if ds else None
    return {"intent":"compare","products":rows,"comparison_table":table,"explanation":f"{a['part_number']}와 {b['part_number']} 비교 결과입니다. 스펙표와 함께 사양서 다운로드도 함께 제공합니다."}

def answer_from_query(query: str) -> Dict[str, Any]:
    q = query.lower()
    if "비교" in q or " vs " in q:
        parts = [p.strip() for p in re.split(r"vs|비교|,|\n", query, flags=re.I) if p.strip()]
        if len(parts) >= 2:
            return compare_parts(parts[:2])
        return {"intent":"compare","error":"비교할 2개 품번을 입력해 주세요."}
    requested_type = detect_requested_file_type(query)
    matches = match_part(query)
    if requested_type or "다운로드" in q or "자료" in q or "파일" in q or "사양서" in q:
        if not matches:
            return {"intent":"file_download","error":"해당 품번의 파일을 찾지 못했습니다."}
        row = matches[0]
        f = best_matching_file(row, requested_type)
        if not f:
            return {"intent":"file_download","error":"해당 품번에 연결된 파일이 없습니다."}
        return {"intent":"file_download","part_number": row["part_number"], "file_type": f.get("file_type"), "file_name": f.get("file_name"), "download_url": f"/files/{f.get('id')}", "explanation": f"{row['part_number']}의 {f.get('file_type')} 파일을 찾았습니다."}
    if "추천" in q or "대체" in q:
        items = matches[:5]
        for row in items:
            ds = best_matching_file(row, "datasheet")
            row["datasheet_download_url"] = f"/files/{ds['id']}" if ds else None
        return {"intent":"recommend","items":items,"explanation":"현재 업로드된 datasheet 기준으로 후보를 정리했습니다."}
    items = matches[:10]
    for row in items:
        ds = best_matching_file(row, "datasheet")
        row["datasheet_download_url"] = f"/files/{ds['id']}" if ds else None
        row["spec_summary"] = summarize_specs(row)
    return {"intent":"search","items":items,"explanation":f"업로드된 datasheet 기준으로 {len(items)}개 후보를 찾았습니다."}

@app.get("/health")
def health():
    parts = grouped_parts()
    file_count = len(table_select_all(PRODUCT_FILES_TABLE))
    return {"ok": True, "rows": len(parts), "files_count": file_count, "storage_bucket": STORAGE_BUCKET}

@app.post("/query")
def query(req: QueryRequest):
    return answer_from_query(req.query)

@app.post("/compare")
def compare(req: CompareRequest):
    return compare_parts(req.part_numbers)

@app.post("/admin/upload-product-file")
async def upload_product_file(
    file: UploadFile = File(...),
    part_number: str = Form(...),
    file_type: str = Form(...),
    description: str = Form(""),
    manufacturer: str = Form(""),
    package: str = Form(""),
    cct_k: str = Form(""),
    cri: str = Form(""),
    luminous_flux_lm: str = Form(""),
    efficacy_lm_w: str = Form(""),
    forward_voltage_typ_v: str = Form(""),
    test_current_ma: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None),
):
    check_admin_token(x_admin_token)
    file_bytes = await file.read()
    storage_path = upload_file_to_storage(file_bytes, file.filename, part_number, file_type)
    insert_product_file({
        "part_number": part_number, "file_type": file_type, "file_name": file.filename, "storage_path": storage_path,
        "description": description, "manufacturer": manufacturer or None, "package": package or None, "cct_k": cct_k or None,
        "cri": cri or None, "luminous_flux_lm": luminous_flux_lm or None, "efficacy_lm_w": efficacy_lm_w or None,
        "forward_voltage_typ_v": forward_voltage_typ_v or None, "test_current_ma": test_current_ma or None,
    })
    return {"ok": True, "message": "파일 업로드 완료", "part_number": part_number, "file_type": file_type, "storage_path": storage_path}

@app.get("/admin/product-files")
def admin_product_files(part_number: Optional[str] = Query(default=None), x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    return {"items": list_product_files(part_number)}

@app.delete("/admin/product-files/{file_id}")
def admin_delete_product_file(file_id: str, x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    delete_product_file(file_id)
    return {"ok": True, "message": "파일 삭제 완료"}

@app.get("/files/{file_id}")
def file_redirect(file_id: str):
    row = get_product_file(file_id)
    signed = get_signed_download_url(str(row.get("storage_path")), 3600)
    if not signed:
        raise HTTPException(status_code=500, detail="다운로드 링크 생성에 실패했습니다.")
    return RedirectResponse(url=signed)

@app.get("/admin/sample")
def sample():
    return FileResponse(BASE_DIR / "data" / "sample_product_files.csv", filename="sample_product_files.csv")

app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")
