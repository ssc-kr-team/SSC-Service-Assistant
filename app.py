
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

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

BASE_DIR = Path(__file__).resolve().parent
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "korea_lighting_2026")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
INTERNAL_MANUFACTURERS = [x.strip() for x in os.getenv("INTERNAL_MANUFACTURERS", "Seoul Semiconductor,Seoul Viosys,SSC,SVC").split(",") if x.strip()]
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
PRODUCTS_TABLE = os.getenv("SUPABASE_PRODUCTS_TABLE", "products")
REVIEW_TABLE = os.getenv("SUPABASE_REVIEW_TABLE", "review_candidates")
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
DISPLAY_NAME = {
    "part_number":"품번","manufacturer":"제조사","brand":"브랜드","series":"시리즈","status":"상태",
    "application":"적용분야","package":"패키지","cct_k":"CCT(K)","cri":"CRI","luminous_flux_lm":"광속(lm)",
    "efficacy_lm_w":"효율(lm/W)","forward_voltage_typ_v":"Vf Typ(V)","test_current_ma":"시험전류(mA)",
    "macadam_step":"맥아덤","price_level":"가격수준","lead_time_weeks":"납기(주)",
    "replacement_for":"대체대상","competitor_brand":"경쟁브랜드","remark":"비고"
}
COMPARE_FIELDS = ["manufacturer","brand","series","part_number","status","application","package","cct_k","cri","luminous_flux_lm","efficacy_lm_w","forward_voltage_typ_v","test_current_ma","macadam_step","price_level","lead_time_weeks","replacement_for","competitor_brand","remark"]
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

app = FastAPI(title="Supabase Admin UI Product Chatbot", version="3.0.0")
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

class ApproveCandidateRequest(BaseModel):
    candidate_id: str
    mode: str = "upsert"

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

def list_candidates() -> List[Dict[str, Any]]:
    return table_select_all(REVIEW_TABLE)

def save_candidate(payload: Dict[str, Any]):
    sb = get_client()
    sb.table(REVIEW_TABLE).insert(payload).execute()

def delete_candidate(candidate_id: str):
    sb = get_client()
    sb.table(REVIEW_TABLE).delete().eq("candidate_id", candidate_id).execute()

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

def compare_products(df: pd.DataFrame, part_numbers: List[str]) -> Dict[str, Any]:
    if len(part_numbers) != 2:
        raise ValueError("비교는 정확히 2개 품번이 필요합니다.")
    found = []
    for pn in part_numbers:
        m = df[df["part_number"].astype(str).str.lower() == pn.lower()]
        if m.empty:
            raise ValueError(f"품번을 찾을 수 없습니다: {pn}")
        found.append(m.iloc[0])
    a, b = found
    table = []
    summary = []
    for field in COMPARE_FIELDS:
        table.append({"field": field, "label": DISPLAY_NAME.get(field, field), a["part_number"]: a.get(field, None), b["part_number"]: b.get(field, None)})
    if pd.notna(a.get("efficacy_lm_w")) and pd.notna(b.get("efficacy_lm_w")):
        if a["efficacy_lm_w"] > b["efficacy_lm_w"]: summary.append(f"효율은 {a['part_number']}가 우위입니다.")
        elif a["efficacy_lm_w"] < b["efficacy_lm_w"]: summary.append(f"효율은 {b['part_number']}가 우위입니다.")
    if pd.notna(a.get("cri")) and pd.notna(b.get("cri")):
        if a["cri"] > b["cri"]: summary.append(f"연색성은 {a['part_number']}가 우위입니다.")
        elif a["cri"] < b["cri"]: summary.append(f"연색성은 {b['part_number']}가 우위입니다.")
    if str(a.get("application")) != str(b.get("application")):
        summary.append(f"적용분야는 {a['part_number']}={a.get('application')}, {b['part_number']}={b.get('application')} 입니다.")
    return {"products":[a.where(pd.notnull(a), None).to_dict(), b.where(pd.notnull(b), None).to_dict()], "comparison_table":table, "summary":summary, "warning":"광속 및 효율은 서로 다른 Test Current 기준일 수 있으므로 단순 비교에 주의가 필요합니다.", "explanation":" ".join(summary) if summary else "주요 비교표를 확인해 주세요."}

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

    if " vs " in q or "비교" in q:
        tokens = [t for t in re.split(r"vs|비교|,|\\n", query, flags=re.IGNORECASE) if t.strip()]
        candidates = []
        for token in tokens:
            found = search_by_keyword(df, token).drop_duplicates(subset=["part_number"]).head(1)
            if not found.empty:
                candidates.append(found.iloc[0]["part_number"])
        if len(candidates) >= 2:
            data = compare_products(df, candidates[:2]); data["intent"] = "compare"; return data
        return {"intent":"compare","error":"비교할 2개 품번을 찾지 못했습니다."}

    if "추천" in q or "대체" in q:
        rec = recommend_our_products(df, query, 5)
        items = rows_to_records(rec)
        expl = "우리 회사 제품 우선 기준으로 추천했습니다. "
        if items:
            expl += "상위 후보는 " + ", ".join([str(i.get("part_number")) for i in items[:3]]) + " 입니다."
        else:
            expl = "우리 회사 제품 기준으로 조건에 맞는 추천 후보를 찾지 못했습니다."
        return {"intent":"recommend","items":items,"note":"추천점수는 직접 대체 매칭, CCT, CRI, 적용처, 효율, 가격수준, 납기를 기준으로 계산했습니다.","explanation":expl}

    result = filter_df(df, query)
    items = rows_to_records(result)
    explanation = f"질문 조건을 기준으로 {len(items)}개 제품을 찾았습니다."
    if items:
        explanation += " 우선 확인할 후보는 " + ", ".join([str(i.get("part_number")) for i in items[:3]]) + " 입니다."
    return {"intent":"search","items":items,"count":len(items),"explanation":explanation}

def extract_pdf_text(file_bytes: bytes) -> str:
    if PdfReader is None:
        raise HTTPException(status_code=500, detail="pypdf가 설치되지 않았습니다.")
    reader = PdfReader(io.BytesIO(file_bytes))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            pass
    return "\\n".join(parts)

def find_pattern(text: str, patterns: List[str]) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

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

def extract_candidate_from_text(text: str, filename: str) -> Dict[str, Any]:
    part_number = find_pattern(text, [r"Part\\s*Number[:\\s]+([A-Z0-9\\-_\\/]+)", r"Model[:\\s]+([A-Z0-9\\-_\\/]+)", r"Product\\s*Code[:\\s]+([A-Z0-9\\-_\\/]+)"]) or Path(filename).stem
    manufacturer = find_pattern(text, [r"Manufacturer[:\\s]+([A-Za-z0-9\\s&\\-]+)", r"Brand[:\\s]+([A-Za-z0-9\\s&\\-]+)"]) or ""
    package = find_pattern(text, [r"Package[:\\s]+([A-Za-z0-9\\-\\s]+)", r"Package\\s*Type[:\\s]+([A-Za-z0-9\\-\\s]+)"]) or ""
    cct = find_pattern(text, [r"(\\d{4,5})\\s*K", r"CCT[:\\s]+(\\d{4,5})"])
    cri = find_pattern(text, [r"CRI[:\\s]+(\\d{2})", r"Ra[:\\s]+(\\d{2})"])
    flux = find_pattern(text, [r"Flux[:\\s]+(\\d+\\.?\\d*)\\s*lm", r"Luminous\\s*Flux[:\\s]+(\\d+\\.?\\d*)"])
    efficacy = find_pattern(text, [r"(\\d+\\.?\\d*)\\s*lm/W", r"Efficacy[:\\s]+(\\d+\\.?\\d*)"])
    vf = find_pattern(text, [r"Forward\\s*Voltage[:\\s]+(\\d+\\.?\\d*)", r"Vf[:\\s]+(\\d+\\.?\\d*)"])
    current = find_pattern(text, [r"Test\\s*Current[:\\s]+(\\d+\\.?\\d*)\\s*mA", r"If[:\\s]+(\\d+\\.?\\d*)\\s*mA"])
    return {"part_number": part_number, "manufacturer": manufacturer, "brand": manufacturer, "series":"", "status":"검토대기", "application":"", "package": package, "cct_k": to_number(cct), "cri": to_number(cri), "luminous_flux_lm": to_number(flux), "efficacy_lm_w": to_number(efficacy), "forward_voltage_typ_v": to_number(vf), "test_current_ma": to_number(current), "macadam_step": None, "price_level":"", "lead_time_weeks": None, "replacement_for":"", "competitor_brand": manufacturer, "remark": f"PDF 자동추출 후보 / source={filename}"}

@app.get("/health")
def health():
    df = load_products_df()
    return {"ok": True, "rows": len(df), "review_queue": len(list_candidates()), "files_count": len(list_product_files()), "storage_bucket": STORAGE_BUCKET}

@app.post("/query")
def query(req: QueryRequest):
    try:
        return answer_from_query(load_products_df(), req.query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query 처리 오류: {e}")

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

@app.post("/admin/pdf-extract")
async def pdf_extract(file: UploadFile = File(...), pdf_type: str = "competitor", x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드 가능합니다.")
    file_bytes = await file.read()
    text = extract_pdf_text(file_bytes)
    candidate = extract_candidate_from_text(text, file.filename)
    if pdf_type == "our":
        candidate["competitor_brand"] = ""
        candidate["status"] = "양산"
        if not candidate["manufacturer"]:
            candidate["manufacturer"] = INTERNAL_MANUFACTURERS[0]
        if not candidate["brand"]:
            candidate["brand"] = INTERNAL_MANUFACTURERS[0]
        candidate["remark"] = f"우리회사 PDF 자동추출 후보 / source={file.filename}"
    else:
        candidate["remark"] = f"경쟁사 PDF 자동추출 후보 / source={file.filename}"
    payload = {
        "candidate_id": str(uuid.uuid4()),
        "source_filename": file.filename,
        "pdf_type": pdf_type,
        "candidate": candidate,
        "raw_text_excerpt": text[:3000]
    }
    save_candidate(payload)
    return {"ok": True, "message": "PDF 자동 추출 완료. 관리자 검토 후 반영하세요.", **payload}

@app.get("/admin/candidates")
def candidates(x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    return {"items": list_candidates()}

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
    return {"ok": True, "message": "제품 관련 파일 업로드 완료", "part_number": part_number, "file_type": file_type, "storage_path": storage_path}

@app.get("/admin/product-files")
def admin_product_files(part_number: Optional[str] = Query(default=None), x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    return {"items": list_product_files(part_number)}

@app.delete("/admin/product-files/{file_id}")
def admin_delete_product_file(file_id: str, x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    delete_product_file(file_id)
    return {"ok": True, "message": "파일 삭제 완료"}

@app.post("/admin/approve-candidate")
def approve(req: ApproveCandidateRequest, x_admin_token: Optional[str] = Header(default=None)):
    check_admin_token(x_admin_token)
    items = [x for x in list_candidates() if x["candidate_id"] == req.candidate_id]
    if not items:
        raise HTTPException(status_code=404, detail="검토 후보를 찾지 못했습니다.")
    candidate = items[0]["candidate"]
    df = load_products_df().copy()
    pn = str(candidate.get("part_number","")).strip()
    if not pn:
        raise HTTPException(status_code=400, detail="품번이 없어 반영할 수 없습니다.")
    idxs = df[df["part_number"].astype(str).str.lower() == pn.lower()].index.tolist()
    if req.mode == "append":
        df = pd.concat([df, pd.DataFrame([candidate])], ignore_index=True)
    elif req.mode == "update":
        if not idxs:
            raise HTTPException(status_code=400, detail="기존 품번이 없어 update를 할 수 없습니다.")
        idx = idxs[0]
        for k,v in candidate.items():
            df.at[idx, k] = v
    else:
        if idxs:
            idx = idxs[0]
            for k,v in candidate.items():
                df.at[idx, k] = v
        else:
            df = pd.concat([df, pd.DataFrame([candidate])], ignore_index=True)
    replace_all_products(rows_to_records(df))
    delete_candidate(req.candidate_id)
    return {"ok": True, "message": "DB 반영 완료", "part_number": pn, "mode": req.mode}

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

FRONTEND_DIR = BASE_DIR / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")), reload=True)
