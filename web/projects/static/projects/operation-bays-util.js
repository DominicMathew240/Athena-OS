/* Shared duplicate-name helper (T17): a name for a bay copy that is plainly a
   copy and unique in the list, matching the server's bays.duplicate_bay_name. */
window.duplicateBayName = (name, existingNames) => {
  "use strict";
  const MAX = 80;
  const taken = new Set(existingNames);
  const bounded = suffix => suffix.length >= MAX ? suffix.slice(0, MAX) : `${name.slice(0, MAX - suffix.length)}${suffix}`;
  const first = bounded(" (copy)");
  if (!taken.has(first)) return first;
  let counter = 2;
  while (taken.has(bounded(` (copy) ${counter}`))) counter += 1;
  return bounded(` (copy) ${counter}`);
};
