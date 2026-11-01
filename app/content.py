"""Draft policy and FAQ text. Everything here must be reviewed by a lawyer before launch."""
DRAFT = "Draft for the QuickFix MVP. This text has not had legal review and must be reviewed before QuickFix launches publicly."

FAQS = [
    ("How does booking work?", "Pick a service, tell us where and when, and choose an available professional or let any available professional accept. A booking is confirmed only after a professional accepts it."),
    ("Is my professional verified?", "Professionals apply and a QuickFix administrator reviews each application. A profile shows its status: pending, approved, rejected or suspended. Only approved professionals can receive bookings. We do not currently claim background checks."),
    ("How do I pay?", "Payment is cash (or any manual method you agree with the professional) after the job. QuickFix records whether a booking is paid. Online payments are not available yet."),
    ("What does recurring house help mean?", "You choose weekdays and a date range. QuickFix creates one visit per day. Each visit is confirmed only when a professional accepts it, so some visits may show 'Awaiting a professional'."),
    ("Can I cancel?", "Yes, pending and accepted bookings can be cancelled from My bookings. Cancelling an accepted booking within 2 hours of the start is recorded as a late cancellation."),
    ("What if nobody is available?", "We show nearby times that do have professionals, or you can send an open request that any eligible professional can accept."),
    ("Where do you operate?", "Selected Pune localities only, for now. Choose your locality on the home page."),
]

POLICIES = {
    "privacy": ("Privacy policy", [
        "QuickFix stores your name, email, phone number, saved addresses and booking history so we can run the service.",
        "A professional sees your address and instructions only for bookings assigned to them or open requests they are eligible to accept.",
        "Passwords are stored as one-way hashes. We do not store card details. We do not sell personal data.",
        "You can ask support to correct or delete your account data. Some records (such as bookings and audit logs) may need to be kept.",
        "Email, SMS and WhatsApp messages are not sent in this version; notifications appear inside the app only."]),
    "terms": ("Terms of service", [
        "QuickFix is a marketplace that connects customers with independent home-service professionals. Professionals are not employees of QuickFix.",
        "A booking is a request until a professional accepts it. Prices shown are calculated by QuickFix; estimates and custom quotes may change after inspection.",
        "Customers must provide accurate addresses and a safe working environment. Professionals must follow the professional guidelines.",
        "QuickFix may suspend accounts that break these terms or the safety guidelines.",
        "Dispute handling, liability and governing-law terms must be completed with legal advice before launch."]),
    "cancellation": ("Cancellation rules", [
        "Customers can cancel a pending or accepted booking from My bookings and should give a reason.",
        "Cancelling an accepted booking less than 2 hours before its start time is marked as a late cancellation. Any late-cancellation fee must be decided by the business; none is charged automatically.",
        "Bookings cannot be cancelled online once the professional is on the way or the job has started. Contact support instead.",
        "Rescheduling sets a booking back to pending so the professional can confirm the new time.",
        "Cancelling a recurring schedule cancels all future visits that have not started."]),
    "payments": ("Payment and refund information", [
        "This version supports cash or other manual payment directly with the professional after the job is completed.",
        "The professional (or an administrator) marks the booking as paid. Payment status is tracked separately from booking status.",
        "For estimate and quote services the final amount is set when the job is completed.",
        "Refunds are decided case by case by support and recorded manually; QuickFix does not move money in this version.",
        "Online payments (UPI, cards) require a payment gateway account and are not enabled."]),
    "professional-guidelines": ("Professional guidelines", [
        "Be on time, introduce yourself, and carry out only the agreed work.",
        "Update job status honestly: mark 'On the way' only when you have left, and 'Completed' only when the work is done.",
        "Treat customers and their homes with respect. Use customer addresses and details only for the booking.",
        "Quote final amounts fairly. Do not ask for payment outside the agreed price.",
        "Applications are reviewed by QuickFix. Approval can be withdrawn if guidelines are broken."]),
    "safety": ("Safety information", [
        "Every professional account is reviewed by an administrator before it can receive bookings. This is a review of the application, not a background check.",
        "Share your address only through QuickFix bookings. Do not share OTPs, passwords or bank details with anyone.",
        "If you feel unsafe, ask the professional to leave and contact local emergency services. Then report the issue through Support.",
        "QuickFix does not currently offer insurance or guarantees on work."]),
}
