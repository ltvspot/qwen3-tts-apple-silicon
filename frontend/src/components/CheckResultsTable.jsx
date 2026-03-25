import PropTypes from "prop-types";
import React from "react";
import QAStatusBadge from "./QAStatusBadge";

function formatCheckName(name) {
  return name.replaceAll("_", " ");
}

function formatValue(value) {
  if (value === null || value === undefined) {
    return "n/a";
  }

  if (Number.isInteger(value)) {
    return String(value);
  }

  return value.toFixed(2);
}

export default function CheckResultsTable({ checks }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
          <tr>
            <th className="px-4 py-3">Check</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Message</th>
            <th className="px-4 py-3">Value</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {checks.map((check) => (
            <tr key={check.name}>
              <td className="px-4 py-3 font-medium capitalize text-slate-900">{formatCheckName(check.name)}</td>
              <td className="px-4 py-3">
                <QAStatusBadge status={check.status} />
              </td>
              <td className="px-4 py-3 text-slate-600">{check.message}</td>
              <td className="px-4 py-3 font-mono text-xs text-slate-500">{formatValue(check.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

CheckResultsTable.propTypes = {
  checks: PropTypes.arrayOf(PropTypes.shape({
    message: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    status: PropTypes.oneOf(["pass", "warning", "fail"]).isRequired,
    value: PropTypes.number,
  })).isRequired,
};
