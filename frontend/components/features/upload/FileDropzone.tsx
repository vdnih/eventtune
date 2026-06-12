"use client";

import { useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { cn } from "@/lib/utils";
import { Upload, FileText, X } from "lucide-react";

interface Props {
  onFileSelected: (file: File | null) => void;
  selectedFile: File | null;
  disabled?: boolean;
}

export function FileDropzone({ onFileSelected, selectedFile, disabled }: Props) {
  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      onFileSelected(acceptedFiles[0] ?? null);
    },
    [onFileSelected]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "text/csv": [".csv"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.ms-excel": [".xls"],
    },
    maxFiles: 1,
    disabled,
  });

  if (selectedFile) {
    return (
      <div className="border-2 border-brand-500 bg-brand-50 rounded-xl p-6 flex items-center justify-between">
        <div className="flex items-center gap-3 text-brand-700">
          <FileText className="w-6 h-6 shrink-0" />
          <div>
            <p className="font-medium text-sm">{selectedFile.name}</p>
            <p className="text-xs text-brand-500">
              {(selectedFile.size / 1024).toFixed(1)} KB
            </p>
          </div>
        </div>
        {!disabled && (
          <button
            onClick={() => onFileSelected(null)}
            className="text-gray-400 hover:text-gray-600"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      {...getRootProps()}
      className={cn(
        "border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition",
        isDragActive
          ? "border-brand-500 bg-brand-50"
          : "border-gray-300 hover:border-brand-400 hover:bg-gray-50",
        disabled && "opacity-50 cursor-not-allowed"
      )}
    >
      <input {...getInputProps()} />
      <div className="flex flex-col items-center gap-3 text-gray-500">
        <Upload className="w-10 h-10 text-gray-400" />
        <p className="font-medium text-gray-700">
          {isDragActive ? "ここにドロップ" : "アポリストCSVをドラッグ＆ドロップ"}
        </p>
        <p className="text-xs text-gray-400">またはクリックしてファイルを選択</p>
        <p className="text-xs text-gray-400">.csv / .xlsx / .xls 対応</p>
      </div>
    </div>
  );
}
