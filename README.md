# KmerContam

### Program Requirements
- bbmap (>= 37.23) installed and present on your $PATH
- jellyfish (>= 2.2.6) installed and on your $PATH
- Python 2.7

### Python Package Requirements
- jellyfish (see https://github.com/gmarcais/Jellyfish for installation instructions)
- pysam >= 0.11.2.2

### Usage
- Program takes a folder with paired fastq files as input. Files can be uncompressed, or compressed with gzip/bzip2.
- outputs results to a csv file which you name when calling the script.
- Currently runs through test.py

#### Example
python test.py Fastq_Folder outputname.csv
