import io
import base64
from datetime import datetime, timedelta
import qrcode
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import SeatHold, Booking, BookedSeat, Payment, EntryLog, Seat

def now_local():
    return datetime.now()

def purge_expired_holds(event_id=None):
    now = now_local()
    query = SeatHold.query.filter(SeatHold.expires_at <= now)
    if event_id:
        query = query.filter_by(event_id=event_id)
    query.delete(synchronize_session=False)
    db.session.commit()

def get_seat_map_state(event_id):
    purge_expired_holds(event_id)
    booked_ids = {
        bs.seat_id for bs in BookedSeat.query.join(Booking)
        .filter(BookedSeat.event_id == event_id, Booking.status == "Confirmed").all()
    }
    active_holds = {
        sh.seat_id: sh.user_id for sh in SeatHold.query.filter_by(event_id=event_id).all()
    }
    return booked_ids, active_holds

def hold_seats(event, user_id, seat_ids):
    if event.status != "Published":
        return False, "This event is still in Draft. Publish it first."
    if event.event_datetime <= now_local():
        return False, "Ticket sales are closed for this event."
    if not seat_ids or len(seat_ids) > 6:
        return False, "You must select between 1 and 6 seats per booking."

    purge_expired_holds(event.id)
    booked_ids, active_holds = get_seat_map_state(event.id)

    for sid in seat_ids:
        if sid in booked_ids:
            return False, "One or more selected seats are already booked."
        if sid in active_holds and active_holds[sid] != user_id:
            return False, "One or more selected seats are currently held by another user."

    SeatHold.query.filter_by(event_id=event.id, user_id=user_id).delete()
    expiry = now_local() + timedelta(minutes=10)
    for sid in seat_ids:
        db.session.add(SeatHold(event_id=event.id, seat_id=sid, user_id=user_id, expires_at=expiry))

    db.session.commit()
    return True, "Seats held for 10 minutes. Complete checkout before timer expires."

def confirm_booking(event, user_id):
    purge_expired_holds(event.id)
    holds = SeatHold.query.filter_by(event_id=event.id, user_id=user_id).all()
    if not holds:
        return False, "Your seat hold has expired or no seats were held."

    tier_map = {pt.category: pt.price for pt in event.price_tiers}
    total_amount = 0.0
    seat_items = []

    for h in holds:
        seat = Seat.query.get(h.seat_id)
        price = tier_map.get(seat.category, 0.0)
        total_amount += price
        seat_items.append((seat, price))

    try:
        booking = Booking(event_id=event.id, user_id=user_id, total_amount=total_amount, status="Confirmed")
        db.session.add(booking)
        db.session.flush()

        for seat, price in seat_items:
            db.session.add(BookedSeat(
                booking_id=booking.id,
                event_id=event.id,
                seat_id=seat.id,
                price_paid=price
            ))

        db.session.add(Payment(booking_id=booking.id, amount=total_amount, txn_type="Charge"))
        SeatHold.query.filter_by(event_id=event.id, user_id=user_id).delete()

        # Generate permanent Base64 QR E-Ticket stored directly in database
        qr_payload = f"TICKET|{booking.booking_ref}|{event.event_code}|{booking.qr_token}"
        img = qrcode.make(qr_payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
        booking.qr_image_path = f"data:image/png;base64,{b64_str}"

        db.session.commit()
        return True, f"Booking confirmed! Reference: {booking.booking_ref}"
    except IntegrityError:
        db.session.rollback()
        return False, "Seat conflict detected. Another user completed booking first."

def cancel_booking_with_refund(booking, organizer_cancelled=False):
    if booking.status == "Cancelled":
        return False, "Booking is already cancelled."

    hours_before = (booking.event.event_datetime - now_local()).total_seconds() / 3600.0

    if organizer_cancelled or hours_before >= 168:  # >= 7 days
        refund_pct = 1.0
    elif hours_before >= 48:
        refund_pct = 0.5
    else:
        refund_pct = 0.0

    refund_amount = round(booking.total_amount * refund_pct, 2)
    booking.status = "Cancelled"
    booking.updated_at = now_local()

    BookedSeat.query.filter_by(booking_id=booking.id).delete()

    if refund_amount > 0:
        db.session.add(Payment(booking_id=booking.id, amount=refund_amount, txn_type="Refund"))

    db.session.commit()
    return True, f"Booking cancelled. Refund issued: ₹{refund_amount} ({int(refund_pct * 100)}%)."

def validate_gate_entry(qr_input, event_id, staff_user_id):
    token = qr_input.strip().split("|")[-1]
    booking = Booking.query.filter_by(qr_token=token).first()

    if not booking:
        return False, "INVALID TICKET: QR token not recognized."
    if booking.event_id != int(event_id):
        return False, f"WRONG EVENT: Ticket belongs to '{booking.event.title}'."
    if booking.status != "Confirmed":
        return False, f"INVALID STATUS: Ticket is {booking.status}."

    existing_scan = EntryLog.query.filter_by(booking_id=booking.id).first()
    if existing_scan:
        return False, f"DUPLICATE ENTRY REJECTED: Already scanned at {existing_scan.scanned_at.strftime('%H:%M:%S')}."

    db.session.add(EntryLog(booking_id=booking.id, event_id=booking.event_id, scanned_by=staff_user_id))
    db.session.commit()
    seats_str = ", ".join(bs.seat.label for bs in booking.booked_seats)
    return True, f"ENTRY ALLOWED: {booking.user.name} | Ref: {booking.booking_ref} | Seats: {seats_str}"