import type { Employee } from "./types";

export const EMPLOYEES: Employee[] = [
  { id: 1, full_name: "Élise Laurent", first_name: "Élise", department: "Finance", email: "elise@agnes.eu" },
  { id: 2, full_name: "Marie Dupont", first_name: "Marie", department: "Strategy", email: "marie@agnes.eu" },
  { id: 3, full_name: "Paul Müller", first_name: "Paul", department: "Engineering", email: "paul@agnes.eu" },
  { id: 4, full_name: "Sophie Bernard", first_name: "Sophie", department: "Sales", email: "sophie@agnes.eu" },
  { id: 5, full_name: "Lukas Weber", first_name: "Lukas", department: "Engineering", email: "lukas@agnes.eu" },
  { id: 6, full_name: "Anaïs Roche", first_name: "Anaïs", department: "Marketing", email: "anais@agnes.eu" },
  { id: 7, full_name: "Jonas Schmidt", first_name: "Jonas", department: "Operations", email: "jonas@agnes.eu" },
  { id: 8, full_name: "Camille Petit", first_name: "Camille", department: "People", email: "camille@agnes.eu" },
];

export const employeeById = (id: number) => EMPLOYEES.find((e) => e.id === id);
