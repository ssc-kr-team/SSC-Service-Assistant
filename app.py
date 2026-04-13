
import io
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "korea_lighting_2026")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
INTERNAL_MANUFACTURERS = [x.strip() for x in os.getenv("INTERNAL_MANUFACTURERS", "Seoul Semiconductor,Seoul Viosys,SSC,SVC").split(",") if x.strip()]
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
PRODUCTS_TABLE = os.getenv("SUPABASE_PRODUCTS_TABLE", "products")
PRODUCT_FILES_TABLE = os.getenv("SUPABASE_PRODUCT_FILES_TABLE", "product_files")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "product-files")

COLUMN_ALIASES = {
    "part_number": ["Part_Number", "품번", "PN", "Part No", "PartNo"],
    "manufacturer": ["Manufacturer", "제조사"],
    "brand": ["Brand", "브랜드"],
    "series": ["Series", "시리즈"],
    "status": ["Status", "상태"],
    "application": ["Application", "적용분야", "용도", "적용"],
    "package": ["Package", "패키지"],
    "cct_k": ["CCT_K", "CCT", "색온도", "색온도(K)"],
    "cri": ["CRI", "연색성"],
    "luminous_flux_lm": ["Luminous_Flux_lm", "광속", "광속(lm)"],
    "efficacy_lm_w": ["Efficacy_lm_W", "효율", "효율(lm/W)"],
    "forward_voltage_typ_v": ["Forward_Voltage_Typ_V", "Vf Typ", "순방향전압 Typ"],
    "test_current_ma": ["Test_Current_mA", "시험전류", "전류(mA)", "Test Current"],
    "macadam_step": ["Macadam_Step", "맥아덤", "맥아덤스텝"],
    "price_level": ["Price_Level", "가격수준", "가격"],
    "lead_time_weeks": ["Lead_Time_weeks", "납기", "납기(주)"],
    "replacement_for": ["Replacement_For", "대체대상", "대체품기준"],
    "competitor_brand": ["Competitor_Brand", "경쟁사", "경쟁브랜드"],
    "remark": ["Remark", "비고"],
}
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

app = FastAPI(title="Direct File Management Product Chatbot", version="4.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str

def get_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=500, detail="SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def check_admin_token(token: Optional[str]):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="관리자 토큰이 올바르지 않습니다.")

def to_number(v: Any):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None

def detect_column(df: pd.DataFrame, aliases: List[str]):
    for a in aliases:
        if a in df.columns:
            return a
    return None

def standardize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    for std, aliases in COLUMN_ALIASES.items():
        src = detect_column(df, aliases)
        out[std] = df[src] if src else ""
    for c in ["cct_k","cri","luminous_flux_lm","efficacy_lm_w","forward_voltage_typ_v","test_current_ma","macadam_step","lead_time_weeks"]:
        out[c] = out[c].apply(to_number)
    out = out[out["part_number"].astype(str).str.strip() != ""].reset_index(drop=True)
    return out

def parse_uploaded_dataframe(file_bytes: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(file_bytes))
    return pd.read_excel(io.BytesIO(file_bytes), sheet_name=0)

def clean_record(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in r.items():
        if pd.isna(v):
            out[k] = None
        else:
            out[k] = v
    return out

def table_select_all(table_name: str) -> List[Dict[str, Any]]:
    sb = get_client()
    result = sb.table(table_name).select("*").execute()
    return result.data or []

def load_products_df() -> pd.DataFrame:
    rows = table_select_all(PRODUCTS_TABLE)
    if not rows:
        return pd.DataFrame(columns=list(COLUMN_ALIASES.keys()))
    df = pd.DataFrame(rows)
    for c in ["cct_k","cri","luminous_flux_lm","efficacy_lm_w","forward_voltage_typ_v","test_current_ma","macadam_step","lead_time_weeks"]:
        if c in df.columns:
            df[c] = df[c].apply(to_number)
    return df

def replace_all_products(records: List[Dict[str, Any]]):
    sb = get_client()
    sb.table(PRODUCTS_TABLE).delete().neq("part_number", "").execute()
    batch_size = 500
    for i in range(0, len(records), batch_size):
        sb.table(PRODUCTS_TABLE).insert([clean_record(x) for x in records[i:i+batch_size]]).execute()

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

def rows_to_records(df: pd.DataFrame):
    return df.where(pd.notnull(df), None).to_dict(orient="records")

def is_internal(row: pd.Series) -> bool:
    manu = str(row.get("manufacturer","")).lower()
    brand = str(row.get("brand","")).lower()
    return any(x.lower() in manu or x.lower() in brand for x in INTERNAL_MANUFACTURERS)

def extract_cct(query: str):
    m = re.search(r"(\\d{4,5})\\s*k", query.lower())
    return int(m.group(1)) if m else None

def extract_cri(query: str):
    m = re.search(r"cri\\s*(\\d{2})", query.lower())
    if m: return int(m.group(1))
    m = re.search(r"(\\d{2})\\s*이상", query.lower())
    return int(m.group(1)) if m else None

def search_by_keyword(df: pd.DataFrame, query: str) -> pd.DataFrame:
    tokens = [t.strip() for t in re.split(r"\\s+|,|\\n|/", query) if t.strip()]
    if not tokens:
        return df.head(20)
    mask = pd.Series(False, index=df.index)
    for token in tokens:
        t = token.lower()
        m = (
            df["part_number"].astype(str).str.lower().str.contains(t, na=False)
            | df["series"].astype(str).str.lower().str.contains(t, na=False)
            | df["brand"].astype(str).str.lower().str.contains(t, na=False)
            | df["manufacturer"].astype(str).str.lower().str.contains(t, na=False)
            | df["replacement_for"].astype(str).str.lower().str.contains(t, na=False)
            | df["competitor_brand"].astype(str).str.lower().str.contains(t, na=False)
        )
        mask = mask | m
    return df[mask].head(20)

def filter_df(df: pd.DataFrame, query: str) -> pd.DataFrame:
    q = query.lower()
    result = df.copy()
    cct = extract_cct(query)
    cri = extract_cri(query)
    if cct: result = result[result["cct_k"] == cct]
    if cri: result = result[result["cri"] >= cri]
    if "실내용" in q: result = result[result["application"].astype(str).str.contains("실내용", na=False)]
    if "옥외용" in q: result = result[result["application"].astype(str).str.contains("옥외용", na=False)]
    if "3030" in q: result = result[result["package"].astype(str).str.contains("3030", na=False)]
    if "2835" in q: result = result[result["package"].astype(str).str.contains("2835", na=False)]
    if "양산" in q: result = result[result["status"].astype(str).str.contains("양산", na=False)]
    if result.empty: result = search_by_keyword(df, query)
    return result.head(20)

def price_score(level: str) -> int:
    level = str(level).strip()
    if level == "하": return 100
    if level == "중": return 70
    if level == "상": return 40
    return 50

def recommend_our_products(df: pd.DataFrame, query: str, top_k: int=5) -> pd.DataFrame:
    q = query.lower()
    cct = extract_cct(query)
    cri = extract_cri(query)
    wants_indoor = "실내용" in q
    wants_outdoor = "옥외용" in q
    wants_eff = "고효율" in q
    wants_price = any(w in q for w in ["가격","가성비","저가"])
    internal = df[df.apply(is_internal, axis=1)].copy()
    rows = []
    for _, r in internal.iterrows():
        score = 0
        reasons = []
        if query.lower() in str(r["replacement_for"]).lower():
            score += 50; reasons.append("직접 대체 매칭")
        if query.lower() in str(r["competitor_brand"]).lower():
            score += 20; reasons.append("경쟁사 연관 매칭")
        if cct and r["cct_k"] == cct:
            score += 20; reasons.append(f"CCT {cct}K 일치")
        if cri and pd.notna(r["cri"]) and r["cri"] >= cri:
            score += 15; reasons.append(f"CRI {cri} 이상")
        if wants_indoor and "실내용" in str(r["application"]):
            score += 12; reasons.append("실내용 적합")
        if wants_outdoor and "옥외용" in str(r["application"]):
            score += 12; reasons.append("옥외용 적합")
        if wants_eff and pd.notna(r["efficacy_lm_w"]):
            score += min(15, int(r["efficacy_lm_w"] / 12)); reasons.append("효율 반영")
        if wants_price:
            score += round(price_score(r["price_level"]) / 10); reasons.append("가격수준 반영")
        if pd.notna(r["lead_time_weeks"]) and r["lead_time_weeks"] <= 6:
            score += 6; reasons.append("납기 상대 우수")
        rows.append({**r.to_dict(), "recommendation_score": score, "recommendation_reason": reasons})
    if not rows:
        return pd.DataFrame(columns=list(internal.columns)+["recommendation_score","recommendation_reason"])
    return pd.DataFrame(rows).sort_values(by="recommendation_score", ascending=False).head(top_k)

def detect_requested_file_type(query: str) -> Optional[str]:
    q = query.lower()
    for file_type, keywords in FILE_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in q:
                return file_type
    return None

def best_matching_file(part_number: str, requested_type: Optional[str]) -> Optional[Dict[str, Any]]:
    files = list_product_files(part_number)
    if not files:
        return None
    if requested_type:
        typed = [f for f in files if str(f.get("file_type","")) == requested_type]
        if typed:
            return typed[0]
    datasheets = [f for f in files if str(f.get("file_type","")) == "datasheet"]
    return datasheets[0] if datasheets else files[0]

def answer_from_query(df: pd.DataFrame, query: str) -> Dict[str, Any]:
    q = query.lower()
    requested_type = detect_requested_file_type(query)
    if requested_type or "다운로드" in q or "자료" in q or "파일" in q or "사양서" in q:
        target = search_by_keyword(df, query).head(1)
        if not target.empty:
            row = target.iloc[0]
            file_row = best_matching_file(str(row.get("part_number")), requested_type)
            if file_row:
                return {
                    "intent":"file_download",
                    "part_number": row.get("part_number"),
                    "file_type": file_row.get("file_type"),
                    "file_name": file_row.get("file_name"),
                    "download_url": f"/files/{file_row.get('id')}"
                }
    if "추천" in q or "대체" in q:
        rec = recommend_our_products(df, query, 5)
        items = rows_to_records(rec)
        expl = "우리 회사 제품 우선 기준으로 추천했습니다. "
        if items:
            expl += "상위 후보는 " + ", ".join([str(i.get("part_number")) for i in items[:3]]) + " 입니다."
        else:
            expl = "우리 회사 제품 기준으로 조건에 맞는 추천 후보를 찾지 못했습니다."
        return {"intent":"recommend","items":items,"explanation":expl}
    result = filter_df(df, query)
    items = rows_to_records(result)
    explanation = f"질문 조건을 기준으로 {len(items)}개 제품을 찾았습니다."
    if items:
        explanation += " 우선 확인할 후보는 " + ", ".join([str(i.get("part_number")) for i in items[:3]]) + " 입니다."
    return {"intent":"search","items":items,"count":len(items),"explanation":explanation}

def upload_file_to_storage(file_bytes: bytes, original_filename: str, part_number: str, file_type: str) -> str:
    sb = get_client()
    safe_part = re.sub(r"[^A-Za-z0-9\\-_]", "_", part_number or "unknown")
    safe_name = re.sub(r"[^A-Za-z0-9\\-_.]", "_", original_filename)
    path = f"{safe_part}/{file_type}/{uuid.uuid4().hex}_{safe_name}"
    sb.storage.from_(STORAGE_BUCKET).upload(path, file_bytes, {"content-type": "application/pdf", "upsert": "true"})
    return path

def get_signed_download_url(storage_path: str, expires_in: int = 3600) -> str:
    sb = get_client()
    result = sb.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, expires_in)
    if isinstance(result, dict):
        return result.get("signedURL") or result.get("signed_url") or ""
    return ""

@app.get("/health")
def health():
    df = load_products_df()
    return {"ok": True, "rows": len(df), "files_count": len(list_product_files()), "storage_bucket": STORAGE_BUCKET}

@app.post("/query")
def query(req: QueryRequest):
    return answer_from_query(load_products_df(), req.query)

@app.get("/admin/products")
def admin_products(x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    df = load_products_df()
    return {"items": rows_to_records(df)}

@app.post("/admin/upload-db")
async def upload_db(file: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    ext = Path(file.filename).suffix.lower()
    if ext not in [".xlsx",".xls",".csv"]:
        raise HTTPException(status_code=400, detail="xlsx, xls, csv 파일만 업로드 가능합니다.")
    content = await file.read()
    std = standardize_dataframe(parse_uploaded_dataframe(content, file.filename))
    if std.empty:
        raise HTTPException(status_code=400, detail="유효한 DB 데이터를 찾지 못했습니다.")
    replace_all_products(rows_to_records(std))
    return {"ok": True, "message": "공식 DB 업로드 완료", "rows": len(std), "filename": file.filename}

@app.post("/admin/upload-product-file")
async def upload_product_file(
    file: UploadFile = File(...),
    part_number: str = Form(...),
    file_type: str = Form(...),
    description: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None),
):
    check_admin_token(x_admin_token)
    file_bytes = await file.read()
    storage_path = upload_file_to_storage(file_bytes, file.filename, part_number, file_type)
    insert_product_file({
        "part_number": part_number,
        "file_type": file_type,
        "file_name": file.filename,
        "storage_path": storage_path,
        "description": description,
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
    return FileResponse(BASE_DIR / "data" / "sample_product_master.csv", filename="sample_product_master.csv")

app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")
