import os
import csv
import io
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from dotenv import load_dotenv
from datetime import datetime, date

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
ma = Marshmallow(app)

# ─── Models ───────────────────────────────────────────────────────────────────

CATEGORIES = ["Salary", "Food", "Rent", "Transport", "Shopping", "Healthcare", "Entertainment", "Other"]

class Transaction(db.Model):
    __tablename__ = "transactions"
    id          = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount      = db.Column(db.Float, nullable=False)
    type        = db.Column(db.String(10), nullable=False)   # credit | debit
    category    = db.Column(db.String(50), default="Other")
    note        = db.Column(db.String(500), default="")
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "description": self.description,
            "amount": self.amount,
            "type": self.type,
            "category": self.category,
            "note": self.note,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M"),
        }

with app.app_context():
    db.create_all()

@app.context_processor
def inject_now():
    return {"now": datetime.utcnow()}

# ─── Schema ───────────────────────────────────────────────────────────────────

class TransactionSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Transaction
        load_instance = True

transaction_schema  = TransactionSchema()
transactions_schema = TransactionSchema(many=True)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_filters():
    search   = request.args.get("search", "").strip()
    txn_type = request.args.get("type", "")
    category = request.args.get("category", "")
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to", "")
    return search, txn_type, category, date_from, date_to

def _apply_filters(query, search, txn_type, category, date_from, date_to):
    if search:
        query = query.filter(Transaction.description.ilike(f"%{search}%"))
    if txn_type in ("credit", "debit"):
        query = query.filter(Transaction.type == txn_type)
    if category and category in CATEGORIES:
        query = query.filter(Transaction.category == category)
    if date_from:
        try:
            query = query.filter(Transaction.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(Transaction.created_at <= datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59))
        except ValueError:
            pass
    return query

# ─── UI Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    search, txn_type, category, date_from, date_to = _parse_filters()
    page = request.args.get("page", 1, type=int)

    query = Transaction.query.order_by(Transaction.created_at.desc())
    query = _apply_filters(query, search, txn_type, category, date_from, date_to)
    pagination = query.paginate(page=page, per_page=10, error_out=False)

    all_txns = Transaction.query.all()
    total_credit = sum(t.amount for t in all_txns if t.type == "credit")
    total_debit  = sum(t.amount for t in all_txns if t.type == "debit")
    balance      = total_credit - total_debit

    return render_template(
        "index.html",
        transactions=pagination.items,
        pagination=pagination,
        balance=balance,
        total_credit=total_credit,
        total_debit=total_debit,
        categories=CATEGORIES,
        filters={"search": search, "type": txn_type, "category": category,
                 "date_from": date_from, "date_to": date_to},
    )

@app.route("/dashboard")
def dashboard():
    all_txns = Transaction.query.order_by(Transaction.created_at.asc()).all()
    total_credit = sum(t.amount for t in all_txns if t.type == "credit")
    total_debit  = sum(t.amount for t in all_txns if t.type == "debit")
    balance      = total_credit - total_debit

    # category breakdown
    cat_data = {}
    for t in all_txns:
        cat_data.setdefault(t.category, {"credit": 0, "debit": 0})
        cat_data[t.category][t.type] += t.amount

    # monthly trend (last 6 months)
    from collections import defaultdict
    monthly = defaultdict(lambda: {"credit": 0, "debit": 0})
    for t in all_txns:
        key = t.created_at.strftime("%b %Y")
        monthly[key][t.type] += t.amount
    monthly_labels = list(monthly.keys())[-6:]
    monthly_credit = [monthly[k]["credit"] for k in monthly_labels]
    monthly_debit  = [monthly[k]["debit"]  for k in monthly_labels]

    recent = Transaction.query.order_by(Transaction.created_at.desc()).limit(5).all()

    return render_template(
        "dashboard.html",
        balance=balance,
        total_credit=total_credit,
        total_debit=total_debit,
        total_txns=len(all_txns),
        cat_data=cat_data,
        monthly_labels=monthly_labels,
        monthly_credit=monthly_credit,
        monthly_debit=monthly_debit,
        recent=recent,
    )

@app.route("/add", methods=["POST"])
def add_transaction():
    description = request.form.get("description", "").strip()
    amount      = request.form.get("amount", "")
    txn_type    = request.form.get("type", "")
    category    = request.form.get("category", "Other")
    note        = request.form.get("note", "").strip()

    if not description or not amount or txn_type not in ("credit", "debit"):
        flash("Description, amount and type are required.", "error")
        return redirect(url_for("index"))
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Amount must be a positive number.", "error")
        return redirect(url_for("index"))

    db.session.add(Transaction(description=description, amount=amount,
                               type=txn_type, category=category, note=note))
    db.session.commit()
    flash("Transaction added.", "success")
    return redirect(url_for("index"))

@app.route("/edit/<int:txn_id>", methods=["POST"])
def edit_transaction(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    txn.description = request.form.get("description", txn.description).strip()
    txn.category    = request.form.get("category", txn.category)
    txn.note        = request.form.get("note", txn.note).strip()
    try:
        amt = float(request.form.get("amount", txn.amount))
        if amt <= 0:
            raise ValueError
        txn.amount = amt
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("index"))
    txn.type = request.form.get("type", txn.type)
    db.session.commit()
    flash("Transaction updated.", "success")
    return redirect(url_for("index"))

@app.route("/delete/<int:txn_id>", methods=["POST"])
def delete_transaction(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    db.session.delete(txn)
    db.session.commit()
    flash("Transaction deleted.", "success")
    return redirect(url_for("index"))

@app.route("/import/csv", methods=["POST"])
def import_csv():
    file = request.files.get("csv_file")
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a valid CSV file.", "error")
        return redirect(url_for("index"))

    stream = io.StringIO(file.stream.read().decode("utf-8"), newline=None)
    reader = csv.DictReader(stream)
    imported, skipped = 0, 0

    for row in reader:
        try:
            description = row.get("Description", "").strip()
            txn_type    = row.get("Type", "").strip().lower()
            amount      = float(row.get("Amount", 0))
            category    = row.get("Category", "Other").strip()
            note        = row.get("Note", "").strip()

            if not description or txn_type not in ("credit", "debit") or amount <= 0:
                skipped += 1
                continue

            db.session.add(Transaction(description=description, amount=amount,
                                       type=txn_type, category=category, note=note))
            imported += 1
        except (ValueError, KeyError):
            skipped += 1

    db.session.commit()
    flash(f"Imported {imported} transaction(s). {skipped} row(s) skipped.", "success" if imported else "error")
    return redirect(url_for("index"))

@app.route("/export/csv")
def export_csv():
    search, txn_type, category, date_from, date_to = _parse_filters()
    query = Transaction.query.order_by(Transaction.created_at.desc())
    query = _apply_filters(query, search, txn_type, category, date_from, date_to)
    txns  = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Description", "Type", "Category", "Amount", "Note", "Date"])
    for t in txns:
        writer.writerow([t.id, t.description, t.type, t.category,
                         f"{t.amount:.2f}", t.note, t.created_at.strftime("%Y-%m-%d %H:%M")])
    output.seek(0)
    return Response(output, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=transactions.csv"})

# ─── REST API ─────────────────────────────────────────────────────────────────

@app.route("/api/transactions", methods=["GET"])
def api_list():
    search, txn_type, category, date_from, date_to = _parse_filters()
    page     = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    query = Transaction.query.order_by(Transaction.created_at.desc())
    query = _apply_filters(query, search, txn_type, category, date_from, date_to)
    pag   = query.paginate(page=page, per_page=per_page, error_out=False)

    all_txns     = Transaction.query.all()
    total_credit = sum(t.amount for t in all_txns if t.type == "credit")
    total_debit  = sum(t.amount for t in all_txns if t.type == "debit")

    return jsonify({
        "data": [t.to_dict() for t in pag.items],
        "meta": {
            "page": pag.page,
            "pages": pag.pages,
            "total": pag.total,
            "balance": round(total_credit - total_debit, 2),
            "total_credit": round(total_credit, 2),
            "total_debit": round(total_debit, 2),
        }
    })

@app.route("/api/transactions/<int:txn_id>", methods=["GET"])
def api_get(txn_id):
    return jsonify(Transaction.query.get_or_404(txn_id).to_dict())

@app.route("/api/transactions", methods=["POST"])
def api_create():
    data = request.get_json(force=True) or {}
    description = str(data.get("description", "")).strip()
    txn_type    = data.get("type", "")
    category    = data.get("category", "Other")
    note        = str(data.get("note", "")).strip()

    if not description or txn_type not in ("credit", "debit"):
        return jsonify({"error": "description and type (credit|debit) are required"}), 400
    try:
        amount = float(data.get("amount", 0))
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be a positive number"}), 400

    txn = Transaction(description=description, amount=amount,
                      type=txn_type, category=category, note=note)
    db.session.add(txn)
    db.session.commit()
    return jsonify(txn.to_dict()), 201

@app.route("/api/transactions/<int:txn_id>", methods=["PUT"])
def api_update(txn_id):
    txn  = Transaction.query.get_or_404(txn_id)
    data = request.get_json(force=True) or {}

    if "description" in data:
        txn.description = str(data["description"]).strip()
    if "type" in data and data["type"] in ("credit", "debit"):
        txn.type = data["type"]
    if "category" in data:
        txn.category = data["category"]
    if "note" in data:
        txn.note = str(data["note"]).strip()
    if "amount" in data:
        try:
            amt = float(data["amount"])
            if amt <= 0:
                raise ValueError
            txn.amount = amt
        except (ValueError, TypeError):
            return jsonify({"error": "amount must be a positive number"}), 400

    db.session.commit()
    return jsonify(txn.to_dict())

@app.route("/api/transactions/<int:txn_id>", methods=["DELETE"])
def api_delete(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    db.session.delete(txn)
    db.session.commit()
    return jsonify({"message": f"Transaction {txn_id} deleted"}), 200

@app.route("/api/summary", methods=["GET"])
def api_summary():
    from collections import defaultdict
    all_txns     = Transaction.query.all()
    total_credit = sum(t.amount for t in all_txns if t.type == "credit")
    total_debit  = sum(t.amount for t in all_txns if t.type == "debit")

    by_category = defaultdict(lambda: {"credit": 0, "debit": 0})
    for t in all_txns:
        by_category[t.category][t.type] += t.amount

    return jsonify({
        "balance": round(total_credit - total_debit, 2),
        "total_credit": round(total_credit, 2),
        "total_debit": round(total_debit, 2),
        "total_transactions": len(all_txns),
        "by_category": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_category.items()},
    })

if __name__ == "__main__":
    app.run(debug=True)
