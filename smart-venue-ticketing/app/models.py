import uuid
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # Roles: Admin, Organizer, Customer, GateStaff, Finance
    role = db.Column(db.String(20), nullable=False, default="Customer")
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Venue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    venue_code = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4())[:8].upper())
    name = db.Column(db.String(120), nullable=False)
    city = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    seats = db.relationship("Seat", backref="venue", lazy=True, cascade="all, delete-orphan")

class Seat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)
    section = db.Column(db.String(30), nullable=False)
    row_label = db.Column(db.String(10), nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)
    # Categories: Platinum, Gold, Silver, Accessible
    category = db.Column(db.String(20), nullable=False)

    @property
    def label(self):
        return f"{self.row_label}{self.seat_number}"

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_code = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4())[:8].upper())
    title = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    event_datetime = db.Column(db.DateTime, nullable=False)
    description = db.Column(db.Text)
    # Status: Draft, Published, Cancelled
    status = db.Column(db.String(20), default="Draft")
    venue_id = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)
    organizer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    venue = db.relationship("Venue", backref="events")
    price_tiers = db.relationship("PriceTier", backref="event", lazy=True, cascade="all, delete-orphan")

class PriceTier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    price = db.Column(db.Float, nullable=False)
    early_bird_price = db.Column(db.Float, nullable=True)

class SeatHold(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    seat_id = db.Column(db.Integer, db.ForeignKey("seat.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_ref = db.Column(db.String(36), unique=True, default=lambda: "BK-" + str(uuid.uuid4())[:8].upper())
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    # Status: Confirmed, Cancelled
    status = db.Column(db.String(20), default="Confirmed")
    qr_token = db.Column(db.String(64), unique=True, default=lambda: uuid.uuid4().hex)
    qr_image_path = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    event = db.relationship("Event", backref="bookings")
    user = db.relationship("User", backref="bookings")
    booked_seats = db.relationship("BookedSeat", backref="booking", lazy=True, cascade="all, delete-orphan")

class BookedSeat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    seat_id = db.Column(db.Integer, db.ForeignKey("seat.id"), nullable=False)
    price_paid = db.Column(db.Float, nullable=False)
    seat = db.relationship("Seat")

    __table_args__ = (
        db.UniqueConstraint("event_id", "seat_id", name="uq_event_seat_once"),
    )

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    # Type: Charge, Refund
    txn_type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default="Completed")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class EntryLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), unique=True, nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    scanned_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    scanned_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))