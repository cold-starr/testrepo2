import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'credit' or 'debit'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Transaction {self.id} {self.type} {self.amount}>"


with app.app_context():
    db.create_all()


@app.route("/")
def index():
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).all()
    balance = sum(
        t.amount if t.type == "credit" else -t.amount for t in transactions
    )
    return render_template("index.html", transactions=transactions, balance=balance)


@app.route("/add", methods=["POST"])
def add_transaction():
    description = request.form.get("description", "").strip()
    amount = request.form.get("amount", "")
    txn_type = request.form.get("type", "")

    if not description or not amount or txn_type not in ("credit", "debit"):
        flash("All fields are required and type must be credit or debit.", "error")
        return redirect(url_for("index"))

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Amount must be a positive number.", "error")
        return redirect(url_for("index"))

    txn = Transaction(description=description, amount=amount, type=txn_type)
    db.session.add(txn)
    db.session.commit()
    flash("Transaction added successfully.", "success")
    return redirect(url_for("index"))


@app.route("/delete/<int:txn_id>", methods=["POST"])
def delete_transaction(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    db.session.delete(txn)
    db.session.commit()
    flash("Transaction deleted.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
