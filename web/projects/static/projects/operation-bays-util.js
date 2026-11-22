/* Shared duplicate-name helper (T17): a name for a bay copy that is plainly a
   copy and unique in the list, matching the server's bays.duplicate_bay_name. */
window.duplicateBayName = (name, existingNames) => {
  "use strict";
  const taken = new Set(existingNames);
  const base = `${name} (copy)`;
  if (!taken.has(base)) return base;
  let counter = 2;
  while (taken.has(`${base} ${counter}`)) counter += 1;
  return `${base} ${counter}`;
};
