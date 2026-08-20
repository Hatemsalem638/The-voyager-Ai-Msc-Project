export type PropertyType =
  | "any"
  | "hotel"
  | "apartment"
  | "resort"
  | "villa"
  | "cabin"
  | "cottage"
  | "aparthotel"
  | "hostel";

export const PROPERTY_TYPE_OPTIONS: { value: PropertyType; label: string }[] = [
  { value: "any", label: "Any" },
  { value: "hotel", label: "Hotel" },
  { value: "apartment", label: "Apartment" },
  { value: "resort", label: "Resort" },
  { value: "villa", label: "Villa" },
  { value: "cabin", label: "Cabin" },
  { value: "cottage", label: "Cottage" },
  { value: "aparthotel", label: "Aparthotel" },
  { value: "hostel", label: "Hostel" },
];

export function inferPropertyType(text: string): PropertyType | null {
  const lower = text.toLowerCase();
  for (const option of PROPERTY_TYPE_OPTIONS) {
    if (option.value !== "any" && lower.includes(option.value)) {
      return option.value;
    }
  }
  return null;
}
