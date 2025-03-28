import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa
import glob
import os
import re
import itertools
from pathlib import Path
from tqdm import tqdm


class ParquetPipeline:
    def __init__(
        self,
        trec_input_dir,
        intermediate_dir,
        merged_file,
        final_output_dir,
        partition_size_mb=256,
    ):
        self.trec_input_dir = trec_input_dir
        self.intermediate_dir = intermediate_dir
        self.merged_file = merged_file
        self.final_output_dir = final_output_dir
        self.partition_size_mb = partition_size_mb
        self._prepare_dirs()

        control_chars = "".join(
            map(chr, itertools.chain(range(0x00, 0x20), range(0x7F, 0xA0)))
        )
        self.control_char_re = re.compile("[%s]" % re.escape(control_chars))

    def _prepare_dirs(self):
        os.makedirs(self.intermediate_dir, exist_ok=True)
        os.makedirs(self.final_output_dir, exist_ok=True)

    def remove_control_chars(self, s):
        return self.control_char_re.sub("", s)

    def convert_trec_to_parquet(self):
        all_files = glob.glob(os.path.join(self.trec_input_dir, "*.trec"))
        if not all_files:
            print(f"No .trec files found in {self.trec_input_dir}")
            return False

        print(f"Found {len(all_files)} TREC files to process...")
        saved = 0

        for file_path in tqdm(
            all_files, desc="Converting .trec → .parquet", unit="file"
        ):
            try:
                with open(file_path, "r", encoding="utf8") as f:
                    xml = f.read()
                    xml = xml.replace("&<", "&amp;<")
                    xml = self.remove_control_chars(xml)
                    xml = "<ROOT>" + xml + "</ROOT>"
                    df = pd.read_xml(xml)
                    for col in df.select_dtypes(include=["object"]).columns:
                        df[col] = df[col].astype("category")
                    filename = Path(file_path).stem
                    output_path = os.path.join(
                        self.intermediate_dir, f"{filename}.parquet"
                    )
                    df.to_parquet(output_path, index=False)
                    saved += 1
            except Exception as e:
                print(f"✗ Error processing {file_path}: {e}")
        return saved > 0

    def merge_parquet_files(self):
        print(f"Merging Parquet files from: {self.intermediate_dir}")
        parquet_files = glob.glob(os.path.join(self.intermediate_dir, "*.parquet"))
        if not parquet_files:
            print("No Parquet files found for merging.")
            return False

        writer = None
        for file in tqdm(parquet_files, desc="Merging", unit="file"):
            try:
                df = pd.read_parquet(file)
                for col in df.select_dtypes(include=["object"]).columns:
                    df[col] = df[col].astype("category")
                table = pa.Table.from_pandas(df)
                if writer is None:
                    writer = pq.ParquetWriter(
                        self.merged_file, table.schema, compression="snappy"
                    )
                writer.write_table(table)
            except Exception as e:
                print(f"✗ Error processing {file}: {e}")

        if writer:
            writer.close()
            print(f"✓ Merged file saved: {self.merged_file}")
            return True
        else:
            print("Merge failed: No valid data written.")
            return False

    def split_parquet_file(self):
        print(f"Splitting merged Parquet: {self.merged_file}")

        if not os.path.exists(self.merged_file):
            print(f"Error: Merged Parquet file not found at {self.merged_file}")
            return

        table = pq.read_table(self.merged_file)
        total_rows = table.num_rows
        total_memory = table.nbytes
        rows_per_partition = int(
            (self.partition_size_mb * 1024 * 1024) / (total_memory / total_rows)
        )
        rows_per_partition = max(1, rows_per_partition)

        start_idx = 0
        file_index = 1
        with tqdm(total=total_rows, desc="Splitting", unit="rows") as pbar:
            while start_idx < total_rows:
                end_idx = min(start_idx + rows_per_partition, total_rows)
                partition_table = table.slice(start_idx, end_idx - start_idx)
                output_file = os.path.join(
                    self.final_output_dir, f"split_output_{file_index}.parquet"
                )
                pq.write_table(partition_table, output_file, compression="snappy")
                print(f"✓ Saved: {output_file} ({start_idx}-{end_idx})")
                pbar.update(end_idx - start_idx)
                start_idx = end_idx
                file_index += 1

        print(f"✓ Split complete. Output folder: {self.final_output_dir}")

    def run_all(self):
        print("=== STARTING PARQUET PIPELINE ===")

        if not self.convert_trec_to_parquet():
            print("Stopping: No .trec files processed.")
            return

        if not self.merge_parquet_files():
            print("Stopping: Merge step failed (no Parquet files).")
            return

        self.split_parquet_file()
        print("=== PIPELINE COMPLETE ===")


if __name__ == "__main__":
    import os

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
    data_root = os.path.abspath(os.path.join(project_root, "..", "..", "erisk_shared"))

    year = "2024"

    # Uncomment to debug
    # print(script_dir,"\n")
    # print(project_root,"\n")
    # print(data_root)

    # pipeline = ParquetPipeline(
    #     trec_input_dir=os.path.join(
    #         data_root, "raw", "training_data", year, "new_data"
    #     ),
    #     intermediate_dir=os.path.join(data_root, "parquet", "training_data", year),
    #     merged_file=os.path.join(
    #         data_root, "parquet", "training_data", year, f"merged_output_{year}.parquet"
    #     ),
    #     final_output_dir=os.path.join(
    #         data_root, "parquet", "training_data", year, "partitions"
    #     ),
    #     partition_size_mb=256,
    # )

    # For testing data
    trec_input_dir = os.path.join(data_root, "raw", "erisk25-t1-dataset")
    output_base = os.path.join(data_root, "parquet", "testing")

    pipeline = ParquetPipeline(
        trec_input_dir=trec_input_dir,
        intermediate_dir=output_base,
        merged_file=os.path.join(output_base, "merged_output_testing.parquet"),
        final_output_dir=os.path.join(output_base, "partitions"),
        partition_size_mb=256,
    )

    pipeline.run_all()
