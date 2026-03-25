import PropTypes from "prop-types";
import React from "react";

const STATUS_META = {
  fail: {
    className: "border-rose-200 bg-rose-50 text-rose-700",
    label: "Fail",
  },
  pass: {
    className: "border-emerald-200 bg-emerald-50 text-emerald-700",
    label: "Pass",
  },
  pending: {
    className: "border-slate-200 bg-slate-100 text-slate-600",
    label: "Pending",
  },
  warning: {
    className: "border-amber-200 bg-amber-50 text-amber-700",
    label: "Warning",
  },
};

export default function QAStatusBadge({ label = null, status }) {
  const meta = STATUS_META[status] ?? STATUS_META.pending;

  return (
    <span
      className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${meta.className}`}
      data-status={status}
    >
      {label ?? meta.label}
    </span>
  );
}

QAStatusBadge.propTypes = {
  label: PropTypes.string,
  status: PropTypes.oneOf(["pass", "warning", "fail", "pending"]).isRequired,
};
