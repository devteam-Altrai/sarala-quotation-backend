from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pathlib import Path
import zipfile
import json
from datetime import datetime
import openpyxl
from typing import Dict, Any
import shutil
from fastapi import Body



app = FastAPI()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["http://localhost:5173","https://sarala-quotation-dashboard.vercel.app"],  # React dev server
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/upload/")
async def upload_folder(zip_file: UploadFile = File(...)):
    if not zip_file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files allowed")

    folder_name = zip_file.filename[:-4]  # Remove .zip
    folder_path = UPLOAD_DIR / folder_name

    if folder_path.exists():
        raise HTTPException(status_code=400, detail="Folder already exists")

    temp_zip = UPLOAD_DIR / zip_file.filename
    with open(temp_zip, "wb") as f:
        f.write(await zip_file.read())

    with zipfile.ZipFile(temp_zip, "r") as zip_ref:
        zip_ref.extractall(folder_path)

    temp_zip.unlink()  # Delete the zip after extraction

    # --- Parse Excel file and extract part_no + quantity ---
    excel_file = None
    for file in folder_path.rglob("*.xlsx"):
        excel_file = file
        break

    if not excel_file:
        return {"message": f"Folder '{folder_name}' uploaded, but no Excel file found."}

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        ws = wb.active  # First sheet

        part_no_col = 1  # Column B (index 1)
        qty_col = 4      # Column E (index 4)

        parts_dict = {}
        for row in ws.iter_rows(min_row=2):  # Skip header
            part_no = row[part_no_col].value
            quantity = row[qty_col].value

            if part_no and quantity:
                try:
                    parts_dict[str(part_no).strip()] = int(quantity)
                except Exception:
                    continue

        # Save parts.json
        parts_json_file = folder_path / "parts.json"
        with open(parts_json_file, "w", encoding="utf-8") as f:
            json.dump(parts_dict, f, indent=2)

    except Exception as e:
        return {"message": f"Excel parsing failed: {str(e)}"}

    return {"message": f"Folder '{folder_name}' uploaded.", "parts_loaded": len(parts_dict)}

@app.get("/folders/")
def list_folders():
    return {"folders": [f.name for f in UPLOAD_DIR.iterdir() if f.is_dir()]}


@app.get("/folders/{folder_name}/files/")
def list_files(folder_name: str):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    files = []
    for path in folder_path.rglob("*"):
        if path.is_file():
            files.append(str(path.relative_to(folder_path)))

    return {"files": files}

@app.delete("/folders/{folder_name}/")
def delete_folder(folder_name: str):
    folder_path = UPLOAD_DIR / folder_name

    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    try:
        shutil.rmtree(folder_path)
        return {"message": f"Folder '{folder_name}' has been deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting folder: {str(e)}")


@app.get("/folders/{folder_name}/files/{file_path:path}")
def download_file(folder_name: str, file_path: str):
    full_path = UPLOAD_DIR / folder_name / file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(full_path)


# ✅ MODIFIED: Store cost data per part number (append/update)
@app.post("/folders/{folder_name}/costs/")
async def save_cost_data(folder_name: str, cost_data: Dict[str, Any]):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    cost_file = folder_path / "costs.json"

    # 🔸 Validate part number is provided
    part_no = cost_data.get("filename")
    if not part_no:
        raise HTTPException(status_code=400, detail="Missing 'filename' (part number) in cost data")

    # 🔹 Load existing data (if any)
    existing_data = {}
    if cost_file.exists():
        try:
            with open(cost_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = {}

    # 🔸 Update this part's cost
    cost_data["_saved_at"] = datetime.utcnow().isoformat()
    existing_data[part_no] = cost_data

    # 🔹 Save back to file
    try:
        with open(cost_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving cost data: {str(e)}")

    return {"message": f"Cost data saved for part '{part_no}' in folder '{folder_name}'."}



@app.get("/folders/{folder_name}/costs/")
def get_cost_data(folder_name: str):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    cost_file = folder_path / "costs.json"

    if not cost_file.exists():
        return {"message": "No cost data found.", "cost_data": {}}

    try:
        with open(cost_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cost data: {str(e)}")

    return {"cost_data": data}


@app.get("/folders/{folder_name}/parts/{part_no}")
def get_part_quantity(folder_name: str, part_no: str):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    parts_json_file = folder_path / "parts.json"

    if not parts_json_file.exists():
        raise HTTPException(status_code=404, detail="Part data not found for this folder")

    try:
        with open(parts_json_file, "r", encoding="utf-8") as f:
            parts_dict = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading part data: {str(e)}")

    part_no = part_no.strip()
    if part_no in parts_dict:
        return {"part.no": part_no, "quantity": parts_dict[part_no]}
    else:
        raise HTTPException(status_code=404, detail="Part number not found")

@app.get("/folders_with_dates/")
def list_folders_with_dates():
    if not UPLOAD_DIR.exists():
        raise HTTPException(status_code=404, detail="Upload directory not found")

    folders_info = []
    for f in UPLOAD_DIR.iterdir():
        if f.is_dir():
            upload_time = None
            upload_file = f / "upload.json"
            if upload_file.exists():
                try:
                    with open(upload_file, "r", encoding="utf-8") as uf:
                        upload_data = json.load(uf)
                    upload_time = upload_data.get("upload_date", None)
                except Exception as e:
                    print(f"Error reading upload.json for {f.name}: {e}")
                    upload_time = None
            else:
                upload_time = None  # or datetime fallback if you want

            # fallback if upload.json missing
            if not upload_time:
                upload_time = datetime.fromtimestamp(f.stat().st_mtime).isoformat()

            folders_info.append({
                "name": f.name,
                "upload_date": upload_time
            })

    return JSONResponse(content={"folders": folders_info})



# ✅ New: Save quotation name for a folder
@app.post("/folders/{folder_name}/quotation/")
async def save_quotation_name(folder_name: str, payload: Dict[str, Any]):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    quotation_name = payload.get("quotationName")
    if not quotation_name:
        raise HTTPException(status_code=400, detail="Missing 'quotationName' in request body")

    quotation_file = folder_path / "quotation.json"

    # Save quotation name along with timestamp
    data = {
        "quotationName": quotation_name,
        "saved_at": datetime.utcnow().isoformat()
    }

    try:
        with open(quotation_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving quotation: {str(e)}")

    return {"message": f"Quotation name saved for folder '{folder_name}'", "quotationName": quotation_name}


# ✅ New: Retrieve quotation name for a folder
@app.get("/folders/{folder_name}/quotation/")
async def get_quotation_name(folder_name: str):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    quotation_file = folder_path / "quotation.json"
    if not quotation_file.exists():
        return {"message": "No quotation name found", "quotationName": None}

    try:
        with open(quotation_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading quotation: {str(e)}")

    return data


# ✅ New: Fetch cost data for a specific part number
@app.get("/folders/{folder_name}/costs/{part_no}")
def get_cost_for_part(folder_name: str, part_no: str):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    cost_file = folder_path / "costs.json"
    if not cost_file.exists():
        raise HTTPException(status_code=404, detail="No cost data found")

    try:
        with open(cost_file, "r", encoding="utf-8") as f:
            cost_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cost data: {str(e)}")

    part_no = part_no.strip()
    if part_no in cost_data:
        return {part_no: cost_data[part_no]}
    else:
        raise HTTPException(status_code=404, detail="Cost data for this part not found")

@app.post("/folders/{folder_name}/grandtotal/")
async def save_grand_total(folder_name: str, payload: dict = Body(...)):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    grand_total = payload.get("grand_total")
    if grand_total is None:
        raise HTTPException(status_code=400, detail="Missing 'grand_total' in request body")

    grand_total_file = folder_path / "grand_total.json"
    data = {
        "grand_total": grand_total,
        "saved_at": datetime.utcnow().isoformat()
    }

    try:
        with open(grand_total_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving grand total: {str(e)}")

    return {"message": f"Grand total saved for folder '{folder_name}'.", "grand_total": grand_total}

@app.get("/folders/{folder_name}/grandtotal/")
async def get_grand_total(folder_name: str):
    folder_path = UPLOAD_DIR / folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    total_file = folder_path / "grand_total.json"
    if not total_file.exists():
        return {"grand_total": None}

    try:
        with open(total_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading grand total: {str(e)}")
