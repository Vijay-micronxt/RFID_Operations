"""Tags mapped before tag history existed: fill cycle and warehouse, write one history row each."""

import frappe


def execute():
	for tag in frappe.get_all("RFID Tag", ["name", "status", "serial_no", "current_assignment", "cycle"]):
		if tag.cycle:
			continue
		uses = frappe.get_all("RFID Tag Assignment", {"tag": tag.name}, ["name", "serial_no"], order_by="creation asc")
		for n, asg in enumerate(uses, 1):
			wh = asg.serial_no and frappe.db.get_value("Serial No", asg.serial_no, "warehouse")
			frappe.db.set_value("RFID Tag Assignment", asg.name, {"cycle": n, "warehouse": wh}, update_modified=False)
		wh = tag.serial_no and frappe.db.get_value("Serial No", tag.serial_no, "warehouse")
		frappe.db.set_value("RFID Tag", tag.name, {"cycle": len(uses), "warehouse": wh}, update_modified=False)
		if not frappe.db.exists("RFID Tag Event", {"tag": tag.name}):
			from rfid_retail import rfid

			rfid.log_tag_event(frappe.get_doc("RFID Tag", tag.name), "Correction",
				reason="History starts here (tag existed before history was kept)")
