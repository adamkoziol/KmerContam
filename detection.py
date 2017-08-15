from accessoryFunctions.accessoryFunctions import printtime
# import jellyfish
import shutil
import os
import pysam
import genome_size
import gzip
import bz2
import subprocess
import glob
import run_clark
# from Bio.SeqUtils import GC


# TODO: Add option to try to remove reads that have bad kmers in them - maybe not necessary, but could be useful.
# Currently implemented, but horrendously slow.
class ContamDetect:

    @staticmethod
    def parse_fastq_directory(fastq_folder):
        """
        Should be the first thing called on a ContamDetect object.
        :return: List of fastqpairs in nested array [[forward1, reverse1], [forward2, reverse2]] in fastq_pairs,
        list of single-ended files in fastq_singles
        """
        # Get a list of all fastq files. For some reason, having/not having the slash doesn't seem to matter on the
        # fastqfolder argument. These should be all the common extensions
        fastq_files = glob.glob(fastq_folder + "/*.fastq*")
        fastq_files += glob.glob(fastq_folder + "/*.fq*")
        fastq_pairs = list()
        fastq_singles = list()
        for name in fastq_files:
            # If forward and reverse reads are present, put them in a list of paired files.
            # May need to add support for other naming conventions too. Supports both _R1 and _1 type conventions.
            if "_R1" in name and os.path.isfile(name.replace("_R1", "_R2")):
                fastq_pairs.append([name, name.replace("_R1", "_R2")])
            # Other naming convention support.
            elif "_1" in name and os.path.isfile(name.replace("_1", "_2")):
                fastq_pairs.append([name, name.replace("_1", "_2")])
            # Assume that if we can't find a mate reads are single ended, and add them to the appropriate list.
            elif '_R2' not in name and '_2' not in name:
                fastq_singles.append(name)

        return fastq_pairs, fastq_singles

    def trim_fastqs(self, fastq_pairs, fastq_singles):
        """
        For each pair of fastqs in list passed, uses bbduk to trim those file, and puts them in a tmp directory.
        :param fastq_files: Fastq_pairs list generated by parse_fastq_directory.
        :return:
        """
        # Figure out where bbduk is so that we can use the adapter file.
        cmd = 'which bbduk.sh'
        bbduk_dir = subprocess.check_output(cmd.split()).decode('utf-8')
        bbduk_dir = bbduk_dir.split('/')[:-1]
        bbduk_dir = '/'.join(bbduk_dir)
        # Iterate through pairs, running bbduk and writing the trimmed output to the tmp folder for this run.
        for pair in fastq_pairs:
            out_forward = self.output_file + 'tmp/' + pair[0].split('/')[-1]
            out_reverse = self.output_file + 'tmp/' + pair[1].split('/')[-1]
            cmd = 'bbduk.sh in1={} in2={} out1={} out2={} qtrim=w trimq=20 k=25 minlength=50 forcetrimleft=15' \
                  ' ref={}/resources/adapters.fa hdist=1 tpe tbo threads={}'.format(pair[0], pair[1], out_forward,
                                                                                    out_reverse, bbduk_dir,
                                                                                    str(self.threads))
            with open(self.output_file + 'tmp/junk.txt', 'w') as outjunk:
                subprocess.call(cmd, shell=True, stderr=outjunk)

        # Go through single reads, and run bbduk on them too.
        for single in fastq_singles:
            out_name = self.output_file + 'tmp/' + single.split('/')[-1]
            cmd = 'bbduk.sh in={} out={} qtrim=w trimq=20 k=25 minlength=50 forcetrimleft=15' \
                  ' ref={}/resources/adapters.fa hdist=1 tpe tbo threads={}'.format(single, out_name,
                                                                                    bbduk_dir, str(self.threads))
            with open(self.output_file + 'tmp/junk.txt', 'w') as outjunk:
                subprocess.call(cmd, shell=True, stderr=outjunk)

    def run_jellyfish(self, fastq, threads):
        """
        Runs jellyfish at kmer length of self.kmer_size. Writes kmer sequences to mer_sequences.fasta
        :param fastq: An array with forward reads at index 0 and reverse reads at index 1. Can also handle single reads,
        just input an array of length 1.
        :return: integer num_mers, which is number of kmers in the reads at that kmer size.
        """
        # Send files to check if they're compressed. If they are, create uncompressed version that jellyfish can handle.
        to_remove = list()
        to_use = list()
        for j in range(len(fastq)):
            uncompressed = ContamDetect.uncompress_file(fastq[j])
            if 'bz2' in fastq[j]:
                to_use.append(fastq[j].replace('bz2', ''))
                to_remove.append(fastq[j].replace('.bz2', ''))
            elif 'gz' in fastq[j]:
                to_use.append(fastq[j].replace('.gz', ''))
                to_remove.append(fastq[j].replace('.gz', ''))
            else:
                to_use.append(fastq[j])
        # Run jellyfish! Slightly different commands for single vs paired-end reads.
        if len(to_use) > 1:
            cmd = 'jellyfish count -m ' + str(self.kmer_size) + ' -s 100M --bf-size 100M -t ' + str(threads) + ' -C -F 2 ' +\
                  to_use[0] + ' ' + to_use[1]
        else:
            cmd = 'jellyfish count -m ' + str(self.kmer_size) + ' -s 100M --bf-size 100M -t ' + str(threads) + ' -C -F 1 ' + \
                  to_use[0]
        # os.system(cmd)
        subprocess.call(cmd, shell=True)
        # Get the mer_counts file put into the tmp folder that ends up being deleted.
        os.rename('mer_counts.jf', self.output_file + 'tmp/mer_counts.jf')
        # If we had to uncompress files, remove the uncompressed versions.
        if uncompressed:
            for f in to_remove:
                try:
                    # print(f)
                    os.remove(f)
                except:# Needed in case the file has already been removed - figure out the specific error soon.
                    pass

    def write_mer_file(self, jf_file):
        """
        :param jf_file: .jf file created by jellyfish to be made into a fasta file
        :return: The number of unique kmers in said file.
        """
        # Dump the kmers into a fasta file.
        cmd = 'jellyfish dump {} > {}tmp/mer_sequences.fasta'.format(jf_file, self.output_file)
        subprocess.call(cmd, shell=True)
        # Read in the fasta file so we can assign a unique name to each kmer, otherwise things downstream will complain.
        f = open('{}tmp/mer_sequences.fasta'.format(self.output_file))
        fastas = f.readlines()
        f.close()
        outstr = list()
        num_mers = 0
        # Iterate through fasta, renaming sequences that have our minimum kmer count.
        for i in range(len(fastas)):
            if '>' in fastas[i]:
                num_mers += 1
                if int(fastas[i].replace('>', '')) > 3:
                    outstr.append(fastas[i].rstrip() + '_' + str(num_mers) + '\n' + fastas[i + 1])
        f = open(self.output_file + 'tmp/mer_sequences.fasta', 'w')
        f.write(''.join(outstr))
        f.close()
        return num_mers

    def run_bbmap(self, pair, threads):
        """
        Runs bbmap on mer_sequences.fasta, against mer_sequences.fasta, outputting to test.sam. Important to set
        ambig=all so kmers don't just match with themselves. The parameter pair is expected to be an array with forward
        reads at index 0 and reverse at index 1. If you want to pass single-end reads, just need to give it an array of
        length 1.
        """
        if os.path.isdir('ref'):
            shutil.rmtree('ref')
        cmd = 'bbmap.sh ref=' + self.output_file + 'tmp/mer_sequences.fasta in=' + self.output_file + 'tmp/mer_sequences.fasta ambig=all ' \
              'outm=' + self.output_file + 'tmp/' + pair[0].split('/')[-1] + '.sam subfilter=1 insfilter=0 ' \
                                                     'delfilter=0 indelfilter=0 nodisk threads=' + str(threads)
        # os.system(cmd)
        # print('Running bbmap...')
        with open(self.output_file + 'tmp/junk.txt', 'w') as outjunk:
            subprocess.call(cmd, shell=True, stderr=outjunk)

    @staticmethod
    def uncompress_file(filename):
        """
        If a file is gzipped or bzipped, creates an uncompressed copy of that file in the same folder
        :param filename: Path to file you want to uncompress
        :return: True if the file needed to be uncompressed, otherwise false.
        """
        uncompressed = False
        if ".gz" in filename:
            in_gz = gzip.open(filename, 'rb')
            out = open(filename.replace('.gz', ''), 'wb')
            out.write(in_gz.read())
            out.close()
            uncompressed = True
        elif ".bz2" in filename:
            in_bz2 = bz2.BZ2File(filename, 'rb')
            out = open(filename.replace('.bz2', ''), 'wb')
            out.write(in_bz2.read())
            out.close()
            uncompressed = True
        return uncompressed

    def read_samfile(self, num_mers, fastq):
        """
        :param num_mers: Number of unique kmers for the sample be looked at. Found by write_mer_file.
        :param fastq: Array with forward read filepath at index 0, reverse read filepath at index 1. Alternatively, name
        of single-end read file in array of length 1.
        Parse through the SAM file generated by bbmap to find how often contaminating alleles are present.
        Also calls methods from genome_size.py in order to estimate genome size (good for finding cross-species contam).
        Writes results to user-specified output file.
        """
        i = 1
        # Open up the alignment file for parsing.
        samfile = pysam.AlignmentFile(self.output_file + 'tmp/' + fastq[0].split('/')[-1] + '.sam', 'r')
        bad_kmers = list()
        # samfile = pysam.AlignmentFile('test.sam', 'r')
        for match in samfile:
            # We're interested in full-length matches with one mismatch. This gets us that.
            if "1X" in match.cigarstring and match.query_alignment_length == self.kmer_size:
                query = match.query_name
                reference = samfile.getrname(match.reference_id)
                # query_kcount = float(query.split('_')[-1])
                # ref_kcount = float(reference.split('_')[-1])
                query_kcount = float(query.split('_')[0])
                ref_kcount = float(reference.split('_')[0])
                if query_kcount > ref_kcount:
                    # print(reference, query)
                    high = query_kcount
                    low = ref_kcount
                    if 0.01 < low/high < 0.3:
                        i += 1
                        bad_kmers.append(reference)
                else:
                    # print(query, reference)
                    low = query_kcount
                    high = ref_kcount
                    if 0.01 < low/high < 0.3:
                        i += 1
                        bad_kmers.append(query)
                # Ratios that are very low are likely sequencing errors, and high ratios are likely multiple similar
                # genes within a genome (looking at you, E. coli!)
        # Try to get estimated genome size.
        # Make jellyfish run a histogram.
        genome_size.run_jellyfish_histo(self.output_file)
        # Find total number of mers and peak coverage value so estimated genome size can be calculated.
        peak, total_mers = genome_size.get_peak_kmers(self.output_file + 'tmp/histogram.txt')
        # Calculate the estimated size
        estimated_size = genome_size.get_genome_size(total_mers, peak)
        # Large estimated size means cross species contamination is likely. Run CLARK-light to figure out which species
        # are likely present
        if estimated_size > 10000000 and self.classify:
            printtime('Cross-species contamination suspected! Running CLARK for classification.', self.start)
            run_clark.classify_metagenome('bacteria/', fastq, self.threads)
            clark_results = run_clark.read_clark_output('abundance.csv')
            os.remove('abundance.csv')
        else:
            clark_results = 'NA'
        # Estimate coverage with some shell magic.
        estimated_coverage = ContamDetect.estimate_coverage(estimated_size, fastq)
        # Calculate how often we have potentially contaminating kmers and output results.
        outstr = fastq[0].split('/')[-1] + ',{:.7f},' + str(num_mers) + ',{:.0f},{:.0f},' + clark_results + '\n'
        percentage = (100.0 * float(i)/float(num_mers))
        f = open(self.output_file, 'a+')
        f.write(outstr.format(percentage, estimated_size, estimated_coverage))
        f.close()
        # Should get tmp files cleaned up here so disk space doesn't get overwhelmed if running many samples.
        files = glob.glob(self.output_file + 'tmp/*.sam')
        for f in files:
            os.remove(f)
        return bad_kmers

    def discard_bad_kmers(self, fastq, bad_kmers):
        # TODO: Test this out eventually, see if it improves assembly quality at all. Finish making single-end support
        # work if this is a good idea.
        """
        :param bad_kmers: List of kmers we think are bad, generated by read_samfile
        The plan here is to make a fasta file of not-good kmers (when reading the samfile?) and then run bbduk with that
        file as the reference, discarding the reads that contain exact matches to those kmers.
        :return:
        """
        # First up, read through mer_sequences.fasta and retrieve the bad kmers.
        f = open(self.output_file + 'tmp/mer_sequences.fasta')
        mers = f.readlines()
        f.close()
        bad_mer_list = list()
        # Now actually goes at an acceptable speed. Yay.
        mer_dict = dict()
        for i in range(0, len(mers), 2):
            key = mers[i].replace('>', '')
            key = key.replace('\n', '')
            mer_dict[key] = mers[i + 1]
        for bad_mer in bad_kmers:
            bad_mer_list.append('>' + bad_mer + '\n' + mer_dict[bad_mer])
        f = open(self.output_file + 'tmp/bad_kmers.fasta', 'w')
        f.write(''.join(bad_mer_list))
        f.close()
        if len(fastq) == 2:
            cmd = 'bbduk.sh k=31 in1={} in2={} out1=clean_R1.fastq.gz out2=clean_R2.fastq.gz ref={} maskmiddle=f'.format(fastq[0],
                                                                                                    fastq[1], self.output_file + 'tmp/bad_kmers.fasta')
            with open(self.output_file + 'tmp/junk.txt', 'w') as outjunk:
                subprocess.call(cmd, shell=True, stderr=outjunk)
        else:
            cmd = 'bbduk.sh in={} out=clean1.fq ref={} maskmiddle=f'.format(fastq[0], bad_kmers)

    @staticmethod
    def estimate_coverage(estimated_size, pair):
        """
        :param estimated_size: Estimated size of genome, in basepairs. Found using genome_size.get_genome_size
        :param pair: Array with structure [path_to_forward_reads, path_to_reverse_reads].
        :return: Estimated coverage depth of genome, as an integer.
        """
        # Use some shell magic to find how many basepairs in forward fastq file - cat it into paste, which lets cut take
        # only the second column (which has the sequence), and then count the number of characters.
        if ".gz" in pair[0]:
            cmd = 'zcat ' + pair[0] + ' | paste - - - - | cut -f 2 | wc -c'
        elif ".bz2" in pair[0]:
            cmd = 'bzcat ' + pair[0] + ' | paste - - - - | cut -f 2 | wc -c'
        else:
            cmd = 'cat ' + pair[0] + ' | paste - - - - | cut -f 2 | wc -c'
        number_bp = int(subprocess.check_output(cmd, shell=True))
        # Multiply by length of array (2 if paired files, 1 if single ended).
        number_bp *= len(pair)
        return number_bp/estimated_size

    def __init__(self, args, start):
        self.fastq_folder = args.fastq_folder
        self.output_file = args.output_file
        self.threads = args.threads
        self.kmer_size = args.kmer_size
        self.classify = args.classify
        self.start = start
        if not os.path.isdir(self.output_file + 'tmp'):
            os.makedirs(self.output_file + 'tmp')
        f = open(self.output_file, 'w')
        f.write('File,Percentage,NumUniqueKmers,EstimatedGenomeSize,EstimatedCoverage,CrossContamination\n')
        f.close()



