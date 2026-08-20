import {
  PROPERTY_TYPE_OPTIONS,
  type PropertyType,
} from "../../data/travelSuggestions";

type PropertyTypeFilterProps = {
  value: PropertyType;
  onChange: (value: PropertyType) => void;
};

export function PropertyTypeFilter({
  value,
  onChange,
}: PropertyTypeFilterProps) {
  return (
    <label>
      <span>Property type</span>
      <select
        onChange={(event) => onChange(event.target.value as PropertyType)}
        value={value}
      >
        {PROPERTY_TYPE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
