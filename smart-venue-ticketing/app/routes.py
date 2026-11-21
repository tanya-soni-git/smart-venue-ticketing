from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User, Venue, Seat, Event, PriceTier, Booking, Payment, EntryLog, SeatHold
from app.services import (
    get_seat_map_state, hold_seats, confirm_booking,
    cancel_booking_with_refund, validate_gate_entry
)

main = Blueprint("main", __name__)

def roles_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                flash("Access denied for your role.", "danger")
                return redirect(url_for("main.events"))
            return fn(*args, **kwargs)
        return decorated
    return wrapper

@main.route("/")
def index():
    return redirect(url_for("main.events"))

@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "warning")
            return redirect(url_for("main.register"))
        user = User(
            name=request.form["name"].strip(),
            email=email,
            role=request.form.get("role", "Customer")
        )
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("main.login"))
    return render_template("register.html")

@main.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if user and user.check_password(request.form["password"]) and user.is_active_user:
            login_user(user)
            return redirect(url_for("main.events"))
        flash("Invalid credentials or deactivated account.", "danger")
    return render_template("login.html")

@main.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.login"))

@main.route("/events")
def events():
    city = request.args.get("city", "").strip()
    category = request.args.get("category", "").strip()
    query = Event.query
    if not current_user.is_authenticated or current_user.role == "Customer":
        query = query.filter_by(status="Published")
    if city:
        query = query.join(Venue).filter(Venue.city.ilike(f"%{city}%"))
    if category:
        query = query.filter(Event.category.ilike(f"%{category}%"))
    all_events = query.order_by(Event.event_datetime.asc()).all()
    return render_template("events.html", events=all_events)

@main.route("/events/<int:event_id>", methods=["GET", "POST"])
@login_required
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "hold":
            seat_ids = [int(x) for x in request.form.getlist("seat_ids")]
            ok, msg = hold_seats(event, current_user.id, seat_ids)
            flash(msg, "success" if ok else "danger")
        elif action == "confirm":
            ok, msg = confirm_booking(event, current_user.id)
            flash(msg, "success" if ok else "danger")
            if ok:
                return redirect(url_for("main.my_bookings"))
        return redirect(url_for("main.event_detail", event_id=event.id))

    booked_ids, active_holds = get_seat_map_state(event.id)
    user_holds = SeatHold.query.filter_by(event_id=event.id, user_id=current_user.id).all()
    tier_prices = {pt.category: pt.price for pt in event.price_tiers}
    return render_template(
        "event_detail.html",
        event=event,
        booked_ids=booked_ids,
        active_holds=active_holds,
        user_holds=user_holds,
        tier_prices=tier_prices
    )

@main.route("/my-bookings", methods=["GET", "POST"])
@login_required
def my_bookings():
    if request.method == "POST":
        booking = Booking.query.get_or_404(int(request.form["booking_id"]))
        if booking.user_id == current_user.id:
            ok, msg = cancel_booking_with_refund(booking)
            flash(msg, "info" if ok else "danger")
        return redirect(url_for("main.my_bookings"))
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.created_at.desc()).all()
    return render_template("my_bookings.html", bookings=bookings)

@main.route("/manage", methods=["GET", "POST"])
@login_required
@roles_required("Admin", "Organizer")
def manage():
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "venue" and current_user.role == "Admin":
            rows = int(request.form["rows"])
            cols = int(request.form["cols"])
            venue = Venue(
                name=request.form["name"],
                city=request.form["city"],
                address=request.form["address"],
                capacity=rows * cols
            )
            db.session.add(venue)
            db.session.flush()
            categories = ["Platinum", "Gold", "Silver", "Accessible"]
            for r in range(rows):
                row_char = chr(65 + r)
                cat = categories[min(r, len(categories) - 1)]
                for c in range(1, cols + 1):
                    db.session.add(Seat(venue_id=venue.id, section="Main", row_label=row_char, seat_number=c, category=cat))
            db.session.commit()
            flash(f"Venue '{venue.name}' created with {rows * cols} seats.", "success")

        elif form_type == "event":
            dt = datetime.strptime(request.form["event_datetime"], "%Y-%m-%dT%H:%M")
            ev = Event(
                title=request.form["title"],
                category=request.form["category"],
                event_datetime=dt,
                description=request.form["description"],
                venue_id=int(request.form["venue_id"]),
                organizer_id=current_user.id,
                status="Draft"
            )
            db.session.add(ev)
            db.session.flush()
            for cat in ["Platinum", "Gold", "Silver", "Accessible"]:
                db.session.add(PriceTier(event_id=ev.id, category=cat, price=float(request.form[f"price_{cat}"])))
            db.session.commit()
            flash("Event created in Draft status.", "success")

        elif form_type == "publish":
            ev = Event.query.get_or_404(int(request.form["event_id"]))
            if ev.venue and ev.venue.seats and ev.price_tiers:
                ev.status = "Published"
                db.session.commit()
                flash(f"'{ev.title}' is now Published!", "success")

        elif form_type == "cancel_event":
            ev = Event.query.get_or_404(int(request.form["event_id"]))
            ev.status = "Cancelled"
            for bk in ev.bookings:
                if bk.status == "Confirmed":
                    cancel_booking_with_refund(bk, organizer_cancelled=True)
            db.session.commit()
            flash("Event cancelled and 100% refunds issued to all customers.", "warning")

        return redirect(url_for("main.manage"))

    venues = Venue.query.all()
    events = Event.query.all()
    return render_template("organizer_panel.html", venues=venues, events=events)

@main.route("/gate-scanner", methods=["GET", "POST"])
@login_required
@roles_required("Admin", "GateStaff")
def gate_scanner():
    events = Event.query.filter_by(status="Published").all()
    selected_event_id = request.values.get("event_id", events[0].id if events else None)
    scan_result = None

    if request.method == "POST" and selected_event_id:
        ok, msg = validate_gate_entry(request.form["qr_token"], selected_event_id, current_user.id)
        scan_result = (ok, msg)

    checked_in = EntryLog.query.filter_by(event_id=selected_event_id).count() if selected_event_id else 0
    sold_bookings = Booking.query.filter_by(event_id=selected_event_id, status="Confirmed").count() if selected_event_id else 0
    return render_template(
        "gate_scanner.html",
        events=events,
        selected_event_id=int(selected_event_id) if selected_event_id else None,
        scan_result=scan_result,
        checked_in=checked_in,
        sold_bookings=sold_bookings
    )

@main.route("/dashboard")
@login_required
@roles_required("Admin", "Organizer", "Finance")
def dashboard():
    events = Event.query.all()
    report_rows = []
    for ev in events:
        confirmed = [b for b in ev.bookings if b.status == "Confirmed"]
        tickets_sold = sum(len(b.booked_seats) for b in confirmed)
        gross_revenue = sum(b.total_amount for b in confirmed)
        refunds = sum(
            p.amount for b in ev.bookings
            for p in Payment.query.filter_by(booking_id=b.id, txn_type="Refund").all()
        )
        check_ins = EntryLog.query.filter_by(event_id=ev.id).count()
        fill_pct = round((tickets_sold / ev.venue.capacity) * 100, 1) if ev.venue.capacity else 0
        net_payable = round((gross_revenue - refunds) * 0.95, 2)  # After 5% platform fee
        report_rows.append({
            "event": ev,
            "tickets_sold": tickets_sold,
            "capacity": ev.venue.capacity,
            "fill_pct": fill_pct,
            "gross_revenue": gross_revenue,
            "refunds": refunds,
            "net_payable": net_payable,
            "check_ins": check_ins
        })
    return render_template("dashboard.html", reports=report_rows)