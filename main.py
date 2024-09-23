# Explain:
#   Final paragraph of 5.3.9.2
# Goals
#   Have query engine support reading other table format such as hive

# each interactino with db is a transaction
# concurrency manager interleaves the regulation of transactions from different clients
# recovery manager write record of those transactions in a log so uncommited transactions can be recovered

# file system allows accessing raw disk in blocks.
# in db, each file is treated like a raw disk
# db access each file by virtual blocks(OR translates these blocks into physical blocks using file system)
# db reads block into pages
# db maintain a pool of pages in memory
# db read and write is done on those poled pages
# By only operating in memory, db can control when writing to disk is happening
# Each table, table index and db log are stored in a single file
# Postgresql uses 8kB as block size

# ch3 simpledb.log          Page, Block, FileMgr
# ch4 simpledb.buffer       LogMgr, LogIter, Buffer, BufferMgr
# ch5 simpledb.tx           LogRecord, RecoveryMgr, LockTable, ConcurrencyMgr, BufferList, Transaction
# ch6 simpledb.record       Scheme, Layout, RecordPage, RecordID, TableScan
# ch7 simpledb.metadata     TableMgr,
# ch8 simpledb.query        Scan, Predicate
# ch9 simpledb.parse
# ch10 simpledb.plan

import os

import logging
import random
import threading
import time

# debug,info,waring,error,critical
logging.basicConfig(format='{filename}, at line {lineno}, on {threadName} {asctime}: {message}', style='{', level=logging.INFO, datefmt="%H:%M:%S")
# logging.info('db logging')

class Block:
    def __init__(self, file_name, block_number):
        self.file_name = file_name
        self.block_number = block_number

    def __eq__(self, other):
        return self.file_name == other.file_name and self.block_number == other.block_number

    # called by repr function
    def __repr__(self):
        return "[file name: " + self.file_name + ", block num: " + str(self.block_number) + "]"

    def __hash__(self):
        return hash((self.file_name, self.block_number))

    # When printed directly
    def __str__(self):
        return "[file name: " + self.file_name + ", block num: " + str(self.block_number) + "]"

    # def __str__(self):
    #     return "[file name: " + self.file_name + ", block num: " + str(self.block_number) + "]"

    # implement __eq__, __hash__, __str__

# FileMgr : readBlock(block_number, blockSize) and into a page
# here we are assuming one to one size corrospondence between block and page size

# We populate data in a bytearray and write it to file which triggers sys call to write
# f = open('all_tables', 'wb', buffering=0)
# f.write(page) # will trigger system write as there is no buffering
class Page:
    def __init__(self, data):
        # get either size to create an empty page, or data that needs to put in a page
        # bytearray(data) allocates new one in memory - usage is heavily controlled by Buffer manager
        # self.bb = data uses what is already in the memory
        # log manager(saves it log in memory, and dumps to this page) send bytearay; 
        # buffer manager send length of bytearray
        self.bb = data if isinstance(data, bytearray) else bytearray(data)

    # Write the data at an offset; int is written as is, but str and byte gets its size appended at the beginning
    # callee is responsible to ensure there are required space for the data
    # If there is not enough room to write the data in the current page;
    #   we will need to append a new block; similar to LogMgr.appendLog(b'log_record')
    def setData(self, start, data):
        if isinstance(data, int):
            data_bin = data.to_bytes(4, 'big') # chosing to convert integer to 4 bytes in big endian, which is the same way we write and read numbers
        elif isinstance(data, str): # for type str
            data_bin = data.encode('utf-8')
            data_bin_len = int.to_bytes(len(data_bin), 4, 'big') # size in byte is the same as the length of the string because I am hoping the string content will fall into ascii range
            data_bin = data_bin_len + data_bin
        else: # types i.e. bytes or bytearray
            data_bin_len = int.to_bytes(len(data), 4, 'big')
            data_bin = data_bin_len + data

        data_len = len(data_bin)
        # Do I need to create + new block to the file for data that exceeds boundary?
        self.bb[start:start + data_len] = data_bin
        return data_len 
  

    def getStr(self, start):
        str_len = self.getInt(start)
        return self.bb[start+4 : start+4+str_len].decode()
    
    def getInt(self, start):
        return int.from_bytes(self.bb[start : start+4], 'big')

    def getByte(self, start):
        byte_len = self.getInt(start)
        return self.bb[start+4 : start+4+byte_len]

# Purpose of this class is to write a page to a block
# Read and trigger immediate disk operation( because buffering it set to 0) to ensure data is saved to disk
class FileMgr:
    # https://stackoverflow.com/questions/1466000/difference-between-modes-a-a-w-w-and-r-in-built-in-open-function
    def __init__(self, db_name, block_size, buffer_size): #As of right now db_name and buffer_size is not being used
        self.db_name = db_name,
        self.block_size = block_size
        self.buffer_size = buffer_size
        self._lock = threading.Lock()

    def readBlockToPage(self, block, page):
        with self._lock:
            self.length(block.file_name) # TODO: hack to create an emptyÎ file if none exists; this needs to be replaced with a list of file handle that cache opened file handles
            f = open(block.file_name, 'rb', buffering=0) # Does buffering has any effect on reading?
            f.seek(self.block_size * block.block_number)
            # Making sure we are only reading the block size of the file
            # We want to minimize the number of blocks we are reading from the disk
            # One way query optimize will make plan based on number of potential blocks we need to ready

            # (I think I am emulating the Java version with this if statement here)
            # if we are reading 10th block of an empty file; we return a zeroed out page
            file_content = bytearray(f.read(self.block_size))
            if file_content:
                page.bb = file_content
            else:
                page.bb = bytearray(self.block_size)
            f.close()

    # if file does not exist we create a new one
    def writePageToBlock(self, block, page):
        with self._lock:
            logging.info('Disk write of ' + str(block))
            f = open(block.file_name, 'r+b', buffering=0) # r is used because a prevents seek and w truncates the file
            f.seek(self.block_size * block.block_number)
            f.write(page.bb)
            f.close()

    # Append a new block to the provided (log) file and return the block reference
    def appendEmptyBlock(self, fileName):
        with self._lock:
            f = open(fileName, 'ab', buffering=0) # How does append mode behave if file do not exists?
            new_block_number = self.length(fileName)
            f.seek(self.block_size * new_block_number) # seek doesn't with a mode
            temp_page = Page(self.block_size)
            f.write(temp_page.bb)
            f.close()
        return Block(fileName, new_block_number)

    def length(self, file_name):
        """return the length of file in terms of block. Access through transaction to ensure thread safety."""
        try :
            return os.path.getsize(file_name) // self.block_size 
        except:
            # trying to get number of block in a file that doesn't exist
            # context manager cleanup resources
            with open(file_name, 'wb', buffering=0):
                pass
            return 0 # New file don't have any block in it

        
class LogMgr:
    # Needs access to file manager because if no log file is present we make one with given block size
    def __init__(self, file_mgr, log_file):
        self.file_mgr = file_mgr
        self.log_file = log_file
        self.current_lsn = 0
        self.last_saved_lsn = 0 # last_flushed_lsn

        self.log_page = Page(self.file_mgr.block_size)
        log_block_count = self.file_mgr.length(self.log_file)
        self._lock = threading.Lock()

        if log_block_count:
            # read last block of log file and put it in a page
            self.log_block = Block(self.log_file, log_block_count-1)
            self.file_mgr.readBlockToPage(self.log_block, self.log_page)
        else:
            # create new log, block and page 
            self.log_block = self.file_mgr.appendEmptyBlock(self.log_file)
            self.log_page.setData(0, self.file_mgr.block_size)
            self.file_mgr.writePageToBlock(self.log_block, self.log_page)
            
    # add b'log_record' to current log_page and return current_lsn
    def appendLog(self, log_record):
        with self._lock:
            boundary = self.log_page.getInt(0)
            bytes_needed = len(log_record) + 4 # for writing length of binary blob

            # check if there is room for the new log record on the current page
            if boundary - bytes_needed < 4: # first 4 bytes are reserved
                self.flushPage()
                self.log_block = self.file_mgr.appendEmptyBlock(self.log_file) # appendNewBlock()
                self.log_page = Page(self.file_mgr.block_size) # not present in the book
                self.log_page.setData(0, self.file_mgr.block_size) # at the beginning the page is empty
                boundary = self.log_page.getInt(0)
                self.file_mgr.writePageToBlock(self.log_block, self.log_page) # writing the newly created block/page immidiately emulates always having the latest block/page in log_block/log_page

            offset = boundary - bytes_needed
            self.log_page.setData(offset, log_record) # ACTUAL WRITE
            self.log_page.setData(0, offset) # Update offset for the next write
            self.current_lsn += 1
            return self.current_lsn

    # Log manager manually decides when to write the page to disk
    def flushPage(self, lsn=None):
        # without lsn; flush the log page
        if not lsn:
            self.file_mgr.writePageToBlock(self.log_block, self.log_page)
            self.last_saved_lsn = self.current_lsn # because we will be flushing all logs from the single log page
            return

        # with lsn; dont flush the log page if those lsn were already flushed
        if lsn > self.last_saved_lsn: # TODO: do we need >= instead?
            self.flushPage()

    # this is a stateful function; depends on what block log manager is currently working on
    def iterator(self):
        self.flushPage() # we flush the log page to ensure iteration goes over all log records
        return LogIter(self.file_mgr, self.log_block) # Returning the current block


class LogIter:
    def __init__(self, fm, block):
        self.fm = fm
        self.block = block

    def __iter__(self):
        self.temp_page = Page(self.fm.block_size)
        fm.readBlockToPage(self.block, self.temp_page)
        self.current_offset = self.temp_page.getInt(0)
        return self # returning self because in each loop self.__next__ will be called

    def __next__(self):
        if self.current_offset >= self.fm.block_size: # reached at the end of the block
            self.block = Block(self.block.file_name, self.block.block_number-1) # TODO: Why -1? Doesn't block number start
            if self.block.block_number < 0:
                raise StopIteration()
            else:
                self.fm.readBlockToPage(self.block, self.temp_page)
                self.current_offset = self.temp_page.getInt(0)

        log_record = self.temp_page.getByte(self.current_offset) 
        self.current_offset = self.current_offset + len(log_record) + 4 # 4 bytes tho skip the length of the Byte blob
        return log_record

# pins page to block and tracks pin count
class Buffer:
    def __init__(self, fm, lm):
        self.fm = fm
        self.lm = lm

        self.block = None
        self.page = Page(fm.block_size)
        self.lsn = -1
        self.txnum = -1
        self.pin_count = 0

    #TODO when we might call it as setMod(x, 0)
    def setModified(self, txnum, lsn): # once Transaction sets data, it updates the txnum that updated the buffer, and pos lsn if it was loggable activity
        self.txnum = txnum
        if lsn >= 0: # first lsn value is 1
            self.lsn = lsn

    def assignToBlock(self, block):
        self.flushDirtyBufferWithLog()
        self.block = block
        self.fm.readBlockToPage(block, self.page) # save the requested block to the Buffer's page
        # why we are not incrementing pin count anytime we are reading a block;
        # because, pin count zero could also mean all clients that was using this buffer no longer need it anymore
        self.pin_count = 0

    def flushDirtyBufferWithLog(self):
        if self.txnum >= 0:
            # write ahead log; anytime we are about to flush a buffer; FLUSH THE LOG FIRST
            # This ensures Page 116, (b) doesn't happen when buffer on disk has the data but log do not
            # this line flush (write-ahead) log upto the lsn that modified this buffer
            self.lm.flushPage(self.lsn) # WRITE AHEAD LOG

            self.fm.writePageToBlock(self.block, self.page)
            self.txnum = -1
        # else nothing has happened yet, therefore there is nothing to flush

    def pin(self):
        self.pin_count += 1

    def unpin(self):
        self.pin_count -= 1

# BufferMgr pins Block(which returns a Buffer ref); The Buffer ref is used to unpin the buffer
# BufferMgr does two things.
#   track changes to page(new data) and
#   (delay) write the modified page back to disk. Write happens when
#       1. page is getting pined to a diff block,
#       2. Recovery manager needs to write pages to prevent data loss

# BufferMgr allows multiple clients to access the buffer pool
# Client ask BufferMgr to pin a block to page
# block is already in a page
#   - and that buffer pinned
#   - and that buffer is not pinned
# block is not in any page (we have to evict a page)
#   - all buffer is the buffer pool is pinned
#   - at least one buffer is the buffer pool is not pinned
class BufferMgr:
    # lm gets passed to Buffer class to flush dirty log block
    # fm gets passed to Buffer class to write buffer to page
    def __init__(self, fm, lm, num_buffers):
        self.fm = fm
        self.lm = lm
        self.num_buffers = num_buffers

        # Here are are initiating buffer, but it doesn't do much since the block and page information are filled out later bm is pinning
        self.buffer_pool = [Buffer(self.fm, self.lm) for _ in range(self.num_buffers)]
        self.pool_availability = self.num_buffers
        self._condition = threading.Condition() # Condition is event and lock combined

    def flushAll(self, at_txnum):
        with self._condition:
            for b in bm.buffer_pool:
                if b.txnum == at_txnum:
                    b.flushDirtyBufferWithLog()

    # takes buffer; returns nothing
    def unpin(self, target_buffer):
        logging.info('Unpinning ' + str(target_buffer.block))
        with self._condition:
            target_buffer.unpin()
            if not target_buffer.pin_count > 0:
                self.pool_availability += 1 # No client is using it. New request to pin is now eligible to replace this buffer
                self._condition.notify_all() # wakes up thread waiting on the condition variable; but the lock is not yet released
        # Lock is released after we exit the context manager
        logging.info('Unpinned ' + str(target_buffer.block))

    # takes block; returns buffer
    def pin(self, target_block):
        logging.info('Pinning ' + str(target_block))
        with self._condition:
            b = self.tryToPin(target_block)
            start = time.time()
            while not b and (time.time() - start) < 10: # not b part is a escape hatch
                self._condition.wait(2.0) # Release lock + current thread is put to sleep + auto wakes up after 2 sec and try to pin block again
                b = self.tryToPin(target_block)
            # we tried to pin a few times, and it has been over 10 seconds
            if not b:
                raise Exception("Buffer Pool is full.")
        logging.info('Pinned ' + str(target_block))
        return b

    def tryToPin(self, target_block):
        b = self.findExistingBuffer(target_block) # check if the requested block is already present in the buffer pool
        if not b:
            logging.info('Not in buffer pool ' + str(target_block))
            b = self.chooseUnpinnedBuffer() # requested block is not already in the buffer pool; so find an unpinned buffer
            if not b:
                return None # requested block is neither in buffer pool nor we have any unpinned buffer
            b.assignToBlock(target_block) # found an unpinned buffer; replace its page with requested block

        # if block was already in buffer pool with pin_count non-zero; we do not lose pool availability yet because someone else was already using it
        # if block was already in buffer pool with pin_count zero; we still will lose pool availability because we are about to pin the buffer
        if not b.pin_count > 0:
            self.pool_availability -= 1

        b.pin()
        return b


    # check if the requested block is already present in the buffer pool
    def findExistingBuffer(self, target_block):
        for b in self.buffer_pool:
            if b.block and (b.block == target_block):
                return b
        return None

    # requested block is not already in the buffer pool; so find an unpinned buffer
    def chooseUnpinnedBuffer(self):
        for b in self.buffer_pool:
            if not b.pin_count > 0: # TODO: pin_count = 0 implies no tx pinned any block to this buffer yet
                return b
        return None

# LogManager sees LogRecords are a bytearray
# First int of the bytearray tells us what type of LogRecord it is

# This was originally an interface with op(), txNumber() and undo() specific
# Classes extending this interface also have
#   static writeToLog() to build log byte array
#   Constructor to parse log byte array and extract txnum, block info, block offset, value
#       These parsed values are to be used by aforementioned op(), txNumber() and undo() method
class LogRecord:
    CHECKPOINT = 0
    START = 1
    COMMIT = 2
    ROLLBACK = 3
    SETINT = 4
    SETSTRING = 5

    # write log(byte array) from log parameters; return lsn
    #   writeToLog(lm=lm, op=LogRecord.SETINT, txnum=10, blk_file='log.file', blk_num=10, blk_offset=80, value=100)
    #   will generate appropriate log byte array and call lm.appendLog
    # Equivalent to static method writeToLog of SetStringRecord class
    @staticmethod
    def writeToLog(**log_param):
        if log_param['op'] == LogRecord.CHECKPOINT:
            op_offset = 0

            temp_page = Page(op_offset + 4)
            temp_page.setData(op_offset, log_param['op'])
            logging.info('Logging ' + LogRecord.toString(temp_page.bb))
            return log_param['lm'].appendLog(temp_page.bb)
        elif log_param['op'] == LogRecord.START or log_param['op'] == LogRecord.COMMIT or log_param['op'] == LogRecord.ROLLBACK:
            op_offset = 0
            txnum_offset = op_offset + 4

            temp_page = Page(txnum_offset + 4)
            temp_page.setData(op_offset, log_param['op'])
            temp_page.setData(txnum_offset, log_param['txnum'])
            logging.info('Logging ' + LogRecord.toString(temp_page.bb))
            return log_param['lm'].appendLog(temp_page.bb)
        elif log_param['op'] == LogRecord.SETSTRING:
            op_offset = 0
            txnum_offset = op_offset + 4
            blk_file_offset = txnum_offset + 4
            blk_num_offset = blk_file_offset + len(log_param['blk_file']) + 4
            blk_offset_offset = blk_num_offset + 4
            old_value_offset = blk_offset_offset + 4

            temp_page = Page(old_value_offset + len(log_param['old_val']) + 4)
            temp_page.setData(op_offset, log_param['op'])
            temp_page.setData(txnum_offset, log_param['txnum'])
            temp_page.setData(blk_file_offset, log_param['blk_file'])
            temp_page.setData(blk_num_offset, log_param['blk_num'])
            temp_page.setData(blk_offset_offset, log_param['blk_offset'])
            temp_page.setData(old_value_offset, log_param['old_val'])
            logging.info('Logging ' + LogRecord.toString(temp_page.bb))
            return log_param['lm'].appendLog(temp_page.bb)
        elif log_param['op'] == LogRecord.SETINT:
            op_offset = 0
            txnum_offset = op_offset + 4
            blk_file_offset = txnum_offset + 4
            blk_num_offset = blk_file_offset + len(log_param['blk_file']) + 4
            blk_offset_offset = blk_num_offset + 4
            old_value_offset = blk_offset_offset + 4

            temp_page = Page(old_value_offset + 4)
            temp_page.setData(op_offset, log_param['op'])
            temp_page.setData(txnum_offset, log_param['txnum'])
            temp_page.setData(blk_file_offset, log_param['blk_file'])
            temp_page.setData(blk_num_offset, log_param['blk_num'])
            temp_page.setData(blk_offset_offset, log_param['blk_offset'])
            temp_page.setData(old_value_offset, log_param['old_val'])
            logging.info('Logging ' + LogRecord.toString(temp_page.bb))
            return log_param['lm'].appendLog(temp_page.bb)

    # extract log parameters from log byte array
    # Used when iterating over binary log file, such as rollback and recovery
    # Equivalent: static method of LogRecord interface that returns instances such as SetStringRecord
    @staticmethod
    def createLogRecord(log_bytearray):
        temp_page = Page(log_bytearray)
        op = temp_page.getInt(0)
        if op == LogRecord.START or op == LogRecord.COMMIT or op == LogRecord.ROLLBACK:
            txnum = temp_page.getInt(4)
            return op, txnum
        elif op == LogRecord.CHECKPOINT:
            return op, -1 # checkpoint returns a dummy txnum, which is -1
        elif op == LogRecord.SETINT or op == LogRecord.SETSTRING:
            txnum = temp_page.getInt(4)
            blk_file = temp_page.getStr(8)
            blk_num = temp_page.getInt(8 + (len(blk_file) + 4))
            blk_offset = temp_page.getInt(8 + (len(blk_file) + 4) + 4)
            if op == LogRecord.SETINT:
                old_val = temp_page.getInt(8 + (len(blk_file) + 4) + 4 + 4)
            else:
                old_val = temp_page.getStr(8 + (len(blk_file) + 4) + 4 + 4)
            return op, txnum, blk_file, blk_num, blk_offset, old_val
        else:
            pass # TODO: Read log byte array to append block

    @staticmethod
    def undo(tx, *log_data):
        # TODO: We do not need to call LogRecord.createLogRecord; instead we can pass the log parameters using splat operator
        op, txnum, blk_file, blk_num, blk_offset, old_val = log_data

        # setInt will look for buffer in the pinned buffer list; this is in turns using BM to pin buffer
        temp_blk = Block(blk_file, blk_num)
        tx.pin(temp_blk)
        if op == LogRecord.SETINT:
            tx.setInt(temp_blk, blk_offset, old_val, False)
        elif op == LogRecord.SETSTRING:
            tx.setString(temp_blk, blk_offset, old_val, False)
        else:
            pass # TODO: byte type and block append?
        tx.unpin(temp_blk)

    # from log byte array get log parameters
    # then return human form
    @staticmethod
    def toString(log_bytearray):
        temp_page = Page(log_bytearray)
        op = temp_page.getInt(0)
        if op == LogRecord.CHECKPOINT:
            return '<CHECKPOINT>'
        elif op == LogRecord.START:
            txnum = temp_page.getInt(4)
            return '<START, ' + str(txnum) + '>'
        elif op == LogRecord.COMMIT:
            txnum = temp_page.getInt(4)
            return '<COMMIT, ' + str(txnum) + '>'
        elif op == LogRecord.ROLLBACK:
            txnum = temp_page.getInt(4)
            return '<ROLLBACK, ' + str(txnum) + '>'
        elif op == LogRecord.SETINT:
            txnum = temp_page.getInt(4)
            blk_file = temp_page.getStr(4 + 4)
            blk_num = temp_page.getInt(4 + 4 + (4 + len(blk_file)))
            blk_offset = temp_page.getInt(4 + 4 + (4 + len(blk_file)) + 4)
            old_val = temp_page.getInt(4 + 4 + (4 + len(blk_file)) + 4 + 4)
            return '<SETINT, ' + str(txnum) + ', ' + blk_file + ', ' + str(blk_num) + ', ' + str(blk_offset) + ', ' + str(old_val) + '>'
        elif op == LogRecord.SETSTRING:
            txnum = temp_page.getInt(4)
            blk_file = temp_page.getStr(4 + 4)
            blk_num = temp_page.getInt(4 + 4 + (4 + len(blk_file)))
            blk_offset = temp_page.getInt(4 + 4 + (4 + len(blk_file)) + 4)
            old_val = temp_page.getStr(4 + 4 + (4 + len(blk_file)) + 4 + 4)
            return '<SETSTRING, ' + str(txnum) + ', ' + blk_file + ', ' + str(blk_num) + ', ' + str(blk_offset) + ', ' + str(old_val) + '>'


# RM treats db log as the source of truth; Therefore to maintain durability RM must flush logs to disk before completing a transaction
# Read/process log
#   Write log record
#   roll back transaction
#   recover after system crash

# Proper shutdown:
#   All incomplete transaction should be rolled back
#   All completed transaction should be commited

# Transaction completion:
#   For undo only algorithm, we are forced to flush the buffers to disk before writing commit
#       one problem is if commit log fails, recovery will undo the transaction without commit log
# Transaction Update:
#   If buffer updates are on disk, but not on log, those updates will get reverted by recovery
#       To prevent that from happening, we flush logs before we flush a buffer, for whatever reason i.e. buffer swap

# There are three types of loggable activity
# Start record when a transaction were created
class RecoveryMgr:
    def __init__(self, tx, txnum, lm, bm):
        self.tx = tx
        self.txnum = txnum
        self.lm = lm
        self.bm = bm

        LogRecord.writeToLog(lm=self.lm, op=LogRecord.START, txnum=self.txnum)

    # Undo only recovery explained in Fig 5.7
    # Undo only recovery algorithm forces all buffer to disk before writing(and flushing) the commit log
    # So, when recovering we can go backward and only undo changes if the transaction was not complete(commit/rollback)
    def commit(self):
        # during commit, we flush all buffers modified by a transaction
        # Although this line might call lm.flushPage multiple times, due to lsn logic, not all will get called
        self.bm.flushAll(self.txnum)

        lsn = LogRecord.writeToLog(lm=self.lm, op=LogRecord.COMMIT, txnum=self.txnum)
        self.lm.flushPage(lsn)

    # Performs a single backward pass.
    # Since each Transaction has its own recovery manager, it should know what transaction we are in
    # Go backward in the log and undo all updates() that belongs to the transaction
    # Finally add a rollback log for that transaction
    def rollback(self):
        # Make a single backward pass through the log
        # Each time we see a update log for self.txnum, we call the undo method of transaction
        # Continue until the start record of self.txnum was reached
        for l in self.lm.iterator():
            log_data = LogRecord.createLogRecord(l) # from byte array extract log record information
            op, txnum = log_data[0], log_data[1]
            if txnum == self.txnum:
                if op == LogRecord.START:
                    break
                LogRecord.undo(self.tx, *log_data)

        self.bm.flushAll(self.txnum) # TODO: Flushing buffers should not be mandatory here.
        lsn = LogRecord.writeToLog(lm=self.lm, op=LogRecord.ROLLBACK, txnum=self.txnum)
        self.lm.flushPage(lsn)

    # Undo, followed by Redo
    # Step 1: Go log backwards and undo the updates() that belongs to transaction that were not committed/rolled back.
    # Step 2: Go log forward and redo the updates() that belongs only commited transactions

    # During Transaction updating
    #   If each buffer update flush log page that will result in poor performance as explained in 4.2
    #       Therefore buffer update logs are only flushed when buffer manager is swapping them out or log page is full
    # During Transaction completion
    #   we flush all buffers associated with the transaction
    #   then we write commit log entry <COMMIT, txnum>
    #   then we flush the log page
    # Undo only (Make sure buffers is flushed to disk before updating log so redo step is not necessary)
    #   We know all commited transactions are

    # Performs a single backward pass.
    # Recovery manger is oblivious current state of the database;
    # it writes old values without looking the current value
    # Recovery requirs a dummy tx, meaning a redundent <start x> will be created before <checkpoint>
    #   but it is not a problem since we never look at logs before <Checkpoint> anyway
    def recover(self):
        # go backward and only undo changes if the transaction was not complete(commit/rollback)
        # transaction without commit/rollback are treated as incomplete and their changes should be reversed
        completed_tx = set()
        for l in self.lm.iterator():
            log_data = LogRecord.createLogRecord(l)
            op, txnum = log_data[0], log_data[1]
            if op == LogRecord.CHECKPOINT:
                return

            if op == LogRecord.COMMIT or op == LogRecord.ROLLBACK:
                completed_tx.add(txnum)
                continue
            elif txnum not in completed_tx and (op == LogRecord.SETINT or op == LogRecord.SETSTRING):
                LogRecord.undo(self.tx, *log_data)
            else:
                pass # such as <START, txnum> log record

        # Upon recovery completion; add checkpoint log
        self.bm.flushAll(self.txnum) # TODO: Flushing buffers should not be mandatory here.
        lsn = LogRecord.writeToLog(lm = lm, op = LogRecord.CHECKPOINT)
        self.lm.flushPage(lsn)

    # Transaction calls these set methods to write to log
    # we want to save the old value in the log; so undo recovery can replace current value with this old value
    # Choosing to use static method instead of SetIntRecord.writeToLog
    def setInt(self, target_buffer, block_offset):
        old_val = target_buffer.page.getInt(block_offset)
        return LogRecord.writeToLog(
            lm=lm,
            op=LogRecord.SETINT,
            txnum=self.txnum,
            blk_file=target_buffer.block.file_name,
            blk_num=target_buffer.block.block_number,
            blk_offset=block_offset,
            old_val=old_val
        )

    def setString(self, target_buffer, block_offset):
        old_val = target_buffer.page.getStr(block_offset)
        return LogRecord.writeToLog(
            lm=lm,
            op=LogRecord.SETSTRING,
            txnum=self.txnum,
            blk_file=target_buffer.block.file_name,
            blk_num=target_buffer.block.block_number,
            blk_offset=block_offset,
            old_val=old_val
        )

# LockTable grants locks to a transaction
class LockTable:
    import collections
    _all_locks = collections.defaultdict(int) # TODO: I do not know if there is any hidden bug for using defaultdict

    def __init__(self):
        self._condition = threading.Condition()

    # def getLockVal(self, target_block):
    #     if target_block in LockTable._all_locks:
    #         return LockTable._all_locks[target_block]
    #     else:
    #         return 0

    # similar to BufferMgr.pin
    def sLock(self, target_block):
        with self._condition:
            start = time.time()
            # we will wait if there is a xlock and we have not waited for at least 10 sec to release that xlock
            while LockTable._all_locks[target_block] < 0 and (time.time() - start) < 10:
                self._condition.wait(2.0) # within this 10 seconds, every 2 sec check if xlock was released

            # Since multiple threads are woken up; another thread might race first to xlock before current thread
            # That's is when this check will fail; and prompt the client to try again
            if LockTable._all_locks[target_block] < 0:
                raise Exception('Tx aborted because it waited to long to acquire slock or another Tx raced first to acquire the slock. Try again.')
            LockTable._all_locks[target_block] += 1

    # We use Approximate Deadlock Detection to prevent Tx from waiting to obtain for a lock for too long
    # Here, we prevent deadlock by aborting Tx that is waiting too long(10 sec) for a lock.
    # Long wait time doesn't mean deadlock, it could also mean a lot of data is being written
    # Meaning, our approach react to situation that could potentially lead to deadlock, which may or may not be an actual deadlock
    # similar to BufferMgr.pin
    def xLock(self, target_block):
        with self._condition:
            start = time.time()
            # > 1 is because slock is obtained before attempting to xlock
            # meaning, if a transaction has xlock on a block, it is implies that it also have slock on it
            while LockTable._all_locks[target_block] > 1 and (time.time() - start) < 10:
                self._condition.wait(2.0)

            # see sLock function doc
            if LockTable._all_locks[target_block] > 1:
                raise Exception('Tx aborted because it waited to long to acquire xlock or another Tx raced first to acquire the xlock. Try again.')
            LockTable._all_locks[target_block] = -1

    # release lock on a block
    def unlock(self, target_block):
        with self._condition:
            if LockTable._all_locks[target_block] > 0:
                LockTable._all_locks[target_block] -= 1
            else:
                # TODO: Maybe another alternative is set this entry to zero
                del LockTable._all_locks[target_block]
                self._condition.notify_all()

# Concurrency Manager responsible for correctly executing concurrent transaction; it uses lock to do so.
# We know serial schedules are correct due to proof by contradiction.
# Let us say we have to run following two transactions.
# We want to run T1 first follow by T2. Each transaction has two operations.
#       T1: W(b1) W(b2)
#       T2: W(b1) W(b2)
# Now we want to run both transactions in parallel.
# Therefore, we want to run constituent operations in these two transactions in non-serial schedule.
# Meaning operations from both transactions will interleave.
# Example 1:
#   One example of running those constituent operations in non-serial schedule is W1(b1) W2(b1) W1(b2) W2(b2)
#   This non-serial schedule IS serializable because this schedule is equivalent to running T1 first following by T2
# Example 2:
#   Another example of running those constituent operations in non-serial schedule is W1(b1) W2(b1) W2(b2) W1(b2)
#   This non-serial schedule IS NOT serializable, because at the end B1 blocks contains update from T1 and B2 block contains update from T2
# So when running multiple transaction CM needs to find a non-serial schedule that yields same result as running those transactions in series
# ISOLATION PROPERTY: Is as if we are running on transaction at a time.
# A non-serial schedule is called serializable if it produces some serial schedule.
# CM uses locking to ensure a non-serial schedule is serializable
# Any conflicting transactions are forced to run in series

# In this case CM will grant T1 a xlock on B1. If CM also grant T2 a xblock on B2, then we have a deadlock.
# Both transaction are waiting for the other transcation to release their block(block release only happens when transcation is complete)

# When running multiple transaction CM is responsible for finding a non-serial schedule that is serializable

# Each transaction holds its own CM object
# Each CM object holds the locks held by a transaction in its instance dict
# Each CM object also holds a reference to global lock table
# Each CM object request a lock on behalf of the transaction using the global lock table and appends to the instance dict

# CM object request a lock using the global LockTable object
# But all CM refer to a static instance of lock table
# This static instance of lock table keeps all locks obtained by all transactions
class ConcurrencyMgr:
    _global_locktable = LockTable() # ConcurrencyMgr.db_locktable

    def __init__(self):
        self.tx_locks = {}

    def sLock(self, target_block):
        if target_block not in self.tx_locks:
            ConcurrencyMgr._global_locktable.sLock(target_block)
            self.tx_locks[target_block] = 'S'
        # else CM always has a sLock no the block

    def xLock(self, target_block):
        if not (target_block in self.tx_locks and self.tx_locks[target_block] == 'X'):
            # this block is already in the tx_locks list and we have xLock on it
            self.sLock(target_block)
            ConcurrencyMgr._global_locktable.xLock(target_block)
            self.tx_locks[target_block] = 'X'

    def release(self):
        for block in self.tx_locks.keys():
            ConcurrencyMgr._global_locktable.unlock(block)
        self.tx_locks.clear()


class BufferList:
    def __init__(self, bm):
        self.bm = bm

        self.block_buffer_map = {}
        self.block_pin_history = []

    def pin(self, target_block):
        buf_ref = bm.pin(target_block)
        self.block_buffer_map[target_block] = buf_ref
        self.block_pin_history.append(target_block)

    def unpin(self, target_block):
        bm.unpin(self.block_buffer_map[target_block])
        self.block_pin_history.remove(target_block) # remove the first entry, one instance, of matching block
        if target_block not in self.block_pin_history:
            del self.block_buffer_map[target_block]

    def unpinAll(self):
        for blk in self.block_pin_history:
            bm.unpin(self.block_buffer_map[blk])
        self.block_buffer_map.clear()
        self.block_pin_history.clear()

    def getBuffer(self, target_block):
        return self.block_buffer_map[target_block]

# Everything from a client is a sequence of transaction
# Transaction is a GROUP of operation the behaves as a SINGLE operation

# A single Transaction has follow properties,
#   Atomicity - (Recovery Manger) Either all or nothing will commit.
#   Durability - (Recovery Manger) Commited transaction are permanent.
# Multiple transaction have the following properties,
#   Consistency - (Concurrency Manager)
#       Each transaction will leave the database in valid state(modification rules are predefine and predictable0.
#       For example, if primary key contrains are not met, the transaction will be rolled back.
#       Database engine must detect when a conflict is about to occur and take corrective action(i.e. make one client wait)
#   Isolation - (Concurrency Manager) Concurrent transaction does not interfare with each other. It is as if they ran in series.


# A client's interation with the database is essentially a series of transaction
# At a given time only one transaction open(not commited/rolled back)
# ? A new transaction will imply closing previous transaction
# Transaction is a collection of work done to the database, i.e. a collection of select and update statement
# A transaction is a correctly scheduled call to write data
# Writing data can occur at different granularity level, at data level using setInt/setStr, or block level
# When multiple transactions are running; concurrency manager interleaves these calls to setInt/setStr
# Serial schedule is always correct - proof by contradiction.
# Lets say we have to run following two transactions; We need to run T1 before T2.
#       T1: W(b1) W(b2)
#       T2: W(b1) W(b2)
# Now we want to run both transactions in parallel so that their result is the same as running them in series.
# We want to run both transactions in non-serial schedule.
# A non-serial schedule is called serializable if it produces some serial schedule.
#   - serial schedule ensures we run T1 before T2
#       W1(b1) W1(b2) W2(b1) W2(b2)
#   - non serial schedule
#       W1(b1) W2(b1) W1(b2) W2(b2)
# Non serial schedule is serializable if it produces the same result as some other serial schedule
# A scheduling is correct if and only if it is serializable
# Concurrency manager will generate non-serial schedule that can be serializable
# CM will use locking table to ensure all generate schedules are serializable
# Transaction uses an instance of CM,which holds an instance of lock table, that is used to obtain locks on a block

# lock is imposed per block, exclusive lock and shared lock

# Transaction uses
#   Recovery Manager(read/write logs recording changes to buffer) and (Multiple transactions will write in the log at ths ame time)
#       Any uncommited transaction(either explicit rollback/system crash) must be undone properly by Recovery Manager
#   Concurrency manager to provide controlled access to buffers

# Workflow
# pin the buffer that adds to the internal buffer list held per transaction
# get/set method requres same block reference, which is fetched from the aforementioned list

# Normal db shutdown involves completing all transactions and flushing buffers into disk
class Transaction:
    # Used together to synchronously increase txnum
    _lock = threading.Lock()
    _next_txnum = 0

    def __init__(self, fm, lm, bm):
        self.fm : FileMgr = fm
        self.lm : LogMgr = lm
        self.bm : BufferMgr = bm

        self.txnum = Transaction.get_next_txnum()
        self.cm : ConcurrencyMgr = ConcurrencyMgr()
        self.rm : RecoveryMgr = RecoveryMgr(self, self.txnum, self.lm, self.bm) # I am unsure everytime I am using self.tx inside RM
        self.bufferList : BufferList = BufferList(self.bm)
        # Currently there is no system to prevent new transaction to begin during recovery

    # Transaction lifespan
    def commit(self):
        self.rm.commit()
        logging.info("Commited " + str(self.txnum))
        self.cm.release()
        self.bufferList.unpinAll()

    def rollback(self):
        self.rm.rollback()
        logging.info("Rolled back " + str(self.txnum))
        self.cm.release()
        self.bufferList.unpinAll()

    # TODO: Find out what is the idle place/setup to call recover()? Every startup doesn't make much sense.
    # any single transaction can trigger recovery of the entire database - why?
    # In example, we create a dummy transaction to run recovery? Why not make this a static method?
    def recover(self):
        # Unlike commit/rollback, there is no locking during recovery because the db server is running the recovery in a single transaction
        # Since multiple clients are not running, there is no need for locking - maintaining isolation property
        self.bm.flushAll(self.txnum) # This line is not necessary if recovery is done during startup. Buffer manager is empty at this point.
        self.rm.recover()

    # Transaction buffer access
    def pin(self, target_block):
        # buffer manager holds mapping between page/block mapping for all transaction
        # self.bufferList holds mapping of blocks used by this transaction
        self.bufferList.pin(target_block)

    def unpin(self, target_block):
        self.bufferList.unpin(target_block)

    # Read and returns value (uses CM for locking)
    def getInt(self, target_block, block_offset):
        self.cm.sLock(target_block)
        buf_ref = self.bufferList.getBuffer(target_block) # TODO: this returns None if the block is not pinned by this tx
        return buf_ref.page.getInt(block_offset)

    def getString(self, target_block, block_offset):
        self.cm.sLock(target_block)
        buf_ref = self.bufferList.getBuffer(target_block)
        return buf_ref.page.getStr(block_offset)

    # Write value (Uses CM for locking and RM for logging)
    def setInt(self, target_block, block_offset, new_val, okToLog):
        self.cm.xLock(target_block)
        buf_ref: Buffer = self.bufferList.getBuffer(target_block)
        lsn = -1
        if okToLog:
            lsn = self.rm.setInt(buf_ref, block_offset)
        logging.info('Writing int ' + str(new_val) + ' to ' + str(buf_ref.block) + ' at ' + str(block_offset))
        buf_ref.page.setData(block_offset, new_val)
        buf_ref.setModified(self.txnum, lsn)

    def setString(self, target_block, block_offset, new_val, okToLog):
        self.cm.xLock(target_block)
        buf_ref: Buffer = self.bufferList.getBuffer(target_block)
        lsn = -1
        if okToLog:
            lsn = self.rm.setString(buf_ref, block_offset)
        logging.info('Writing str ' + str(new_val) + ' to ' + str(buf_ref.block) + ' at ' + str(block_offset))
        buf_ref.page.setData(block_offset, new_val)
        buf_ref.setModified(self.txnum, lsn)

    # Transaction file manager access
    # Why we need file access? Can't we make all update through buffer
    def availableBuffers(self):
        return self.bm.pool_availability

    # size and append read and modifies the end of file marker
    # TODO: Size/append obtains a lock on Block(-1); but when it is being added to the list of buffers associated with a transactions?
    def size(self, filename):
        """call fm.length() that returns block count of a file. Acquires lock on dummy block"""
        self.cm.sLock(Block(filename, -1))
        return fm.length(filename)

    # returns the new block references
    def append(self, filename):
        self.cm.xLock(Block(filename, -1))
        return fm.appendEmptyBlock(filename)

    def blockSize(self):
        return self.fm.block_size

    @staticmethod
    def get_next_txnum():
        with Transaction._lock:
            Transaction._next_txnum += 1
        return Transaction._next_txnum

# Schema hold record(row's) schema
# Schema stores a list of triples (field_name, field_type, field_length)
# {field_name : (field_type, field_length)
class Schema:
    """Maintains a list of ``{field_name:{field_type, field_byte_length}}`` for each table"""

    def __init__(self, *field_data):
        self.field_info = {}
        self.pretty_str = ''
        if field_data:
            for f in field_data:
                self.addField(f[0], f[1], f[2])

    def addField(self, field_name, field_type, field_byte_length):
        self.pretty_str += 'name: ' + field_name + ' type: ' + field_type + ' byte_length: ' + str(field_byte_length) + '\n'
        self.field_info[field_name] = {
            'field_type': field_type,
            'field_byte_length': field_byte_length
        }

    def getFields(self):
        return self.field_info.keys()

    def __str__(self):
        return self.pretty_str


# Layout hold record's field and slot side; field offset within a slot
class Layout:
    """Calculates field offset from schema info"""

    def __init__(self, schema):
        self.schema = schema
        self.offset = {} # Holds byte offset for all fields inside a record
        field_pos = 4 # starting at 4 byte mark because the first 4 byte in the slot is allotted for empty flag
        for sk, sv in self.schema.field_info.items():
            self.offset[sk] = field_pos
            field_pos += (sv['field_byte_length'] if sv['field_type'] == 'int' else (sv['field_byte_length'] + 4))
        self.slot_size = field_pos

    def __str__(self):
        return 'Layout :: \n' + str(self.schema) + 'Slot size: ' + str(self.slot_size)

# file is a sequence of blocks
# record files are a sequence of record pages/blocks
# record page contains sequence of slots
# slot are one byte + record

# RM is responsible for interpreting the values in a record blocks/page.
# RM uses Layout(slot size) and Schema(record info) class to update record page

# Tables are a collection of field(columns) and records(row)
# Record manager keeps the structure of the record(spanned/unspanned; homogeneous/nonhomogeneous) in the block
# Implementing 6.2.1, homogeneous, unspanned, fixed-length records
# block/page contains records, we call it record page
class RecordPage: # Also being called Record Manager
    """Writes record data to a table block using layout"""
    def __init__(self, tx, blk, layout):
        self.tx: Transaction = tx
        self.blk: Block = blk
        self.layout: Layout = layout
        self.tx.pin(blk) # TODO: Are we pinning here to ensure tx.get/set does not fail?

    def setInt(self, slot_index, field_name, field_value):
        blk_offset = (self.layout.slot_size * slot_index) + self.layout.offset[field_name]
        self.tx.setInt(self.blk, blk_offset, field_value, True)

    def setString(self, slot_index, field_name, field_value):
        blk_offset = slot_index * self.layout.slot_size + self.layout.offset[field_name]
        self.tx.setString(self.blk, blk_offset, field_value, True)

    def getInt(self, slot_index, field_name):
        blk_offset = (self.layout.slot_size * slot_index) + self.layout.offset[field_name]
        return self.tx.getInt(self.blk, blk_offset)

    def getString(self, slot_index, field_name):
        blk_offset = (slot_index * self.layout.slot_size) + self.layout.offset[field_name]
        return self.tx.getString(self.blk, blk_offset)

    # Mark the slot empty; we are not operating per field, so field parameter is not needed
    def delete(self, slot_index):
        self.tx.setInt(self.blk, slot_index * self.layout.slot_size, 0, True)

    # Zero our all records in the record page
    def format(self):
        logging.info('Format')
        slot_index = 0
        while ((slot_index * self.layout.slot_size) + self.layout.slot_size) < self.tx.fm.block_size:
            self.tx.setInt(self.blk, slot_index * self.layout.slot_size, 0, False)
            for field_name in self.layout.schema.getFields():
                if self.layout.schema.field_info[field_name]['field_type'] == 'int':
                    self.tx.setInt(self.blk, slot_index * self.layout.slot_size + self.layout.offset[field_name], 0, False)
                else:
                    self.tx.setString(self.blk, slot_index * self.layout.slot_size + self.layout.offset[field_name], '', False) # TODO: If we are not putting anything then recovery doesn't know what to do
            slot_index += 1
        logging.info('Formatted')

    def nextEmpty(self, current_slot_index):
        return self.insertAfter(current_slot_index)

    # next empty slot index with empty flag set to 0
    def insertAfter(self, slot_index):
        slot_index += 1
        while ((slot_index * self.layout.slot_size) + self.layout.slot_size) <= self.tx.fm.block_size:
            if not tx.getInt(self.blk, slot_index * self.layout.slot_size):
                tx.setInt(self.blk, slot_index * self.layout.slot_size, 1, True) # Mark slot filled before returning it
                return slot_index
            slot_index += 1
        return -1

    def nextUsed(self, current_slot_index):
        return self.nextAfter(current_slot_index)

    # next used slot index with empty flag set to 1
    def nextAfter(self, slot_index):
        slot_index += 1
        while ((slot_index * self.layout.slot_size) + self.layout.slot_size) <= self.tx.fm.block_size:
            if self.tx.getInt(self.blk, slot_index * self.layout.slot_size):
                return slot_index
            slot_index += 1
        return -1

# Each record in file can be identified by block number and slot number
# These two values put together is called Record Identifier
class RecordID:
    def __init__(self, blk_num, slot_num):
        self.blk_num = blk_num
        self.slot_num = slot_num

    def __eq__(self, other):
        return self.blk_num == other.blk_num and self.slot_num == other.slot_num

    def __str__(self):
        return "[block numer: " + str(self.blk_num) + ", slot number: " + str(self.slot_num) + "]"


# The whole table lives on a single table
# TableScan manages all records on a file
# During constructor it is using tx, layout and table_name to automatically pointing to the first record

# TableScan uses RecordPage to maintain reference to a single Block
# TableScan opens the first block 0 from a file
#   TableScan also need a transaction and layout information
#   The layout is passed to the RecordPage, TableScan use RecordPage object to move from one record to another
#   TableScan maintains a current_slot_index to know which slot it is reading/writing in that block
# Each record in a file can be identified by block number and slot number, these two combined is called RecordID
# next() moves the cursor forward; it also moves to the next block if there is no more record in the current block
class TableScan:
    """Write record to table block using cursor and field name"""

    def __init__(self, tx, table_name, layout):
        """Open tbl_name file and read records at cursor"""
        self.tx = tx
        self.table_name = table_name
        self.file_name = self.table_name + '.tbl'
        self.layout = layout

        self.current_slot_index = -1 # TODO: Book is initializing this value to zero.
        self.rp: RecordPage = None
        if self.tx.size(self.file_name):
            self.moveToBlock(0)
        else:
            self.moveToNewBlock()

    def moveToNewBlock(self):
        if self.rp:
            self.tx.unpin(self.rp.blk)
        new_blk = self.tx.append(self.file_name)
        self.rp = RecordPage(self.tx, new_blk, self.layout) # tx.pin(rp.blk) happening in RecordPage constructor
        self.rp.format()
        self.current_slot_index = -1

    def moveToBlock(self, block_num):
        if self.rp:
            self.tx.unpin(self.rp.blk)
        new_blk = Block(self.file_name, block_num)
        self.rp = RecordPage(self.tx, new_blk, self.layout)
        self.current_slot_index = -1

    # current_slot movement
    def nextRecord(self):
        self.current_slot_index = self.rp.nextAfter(self.current_slot_index)
        while self.current_slot_index < 0:
            if self.rp.blk.block_number == self.tx.size(self.file_name) - 1: #TODO this implies block count start at zero
                return False
            self.moveToBlock(self.rp.blk.block_number + 1) # Moving to new block may not get us a filled out record...
            self.current_slot_index = self.rp.nextAfter(self.current_slot_index) # ...so we continue
        return True

    def nextEmptyRecord(self):
        self.insert()

    # have current_slot_index point to an empty slot
    def insert(self):
        self.current_slot_index = self.rp.insertAfter(self.current_slot_index)
        while self.current_slot_index < 0:
            # we reached at the end of current block
            if self.rp.blk.block_number == tx.size(self.file_name) - 1:
                # this was the final block in the file, therefore append a new block to our table file
                self.moveToNewBlock()
            else:
                # there are more blocks to this file, therefore move to the next block
                self.moveToBlock(self.rp.blk.block_number + 1)
            self.current_slot_index = self.rp.insertAfter(self.current_slot_index)

    def deleteRecord(self):
        self.rp.delete(self.current_slot_index)

    def firstRecord(self):
        self.beforeFirst()

    def beforeFirst(self):
        self.moveToBlock(0)

    # RecordID
    def moveToRecordID(self, rid : RecordID):
        self.moveToBlock(rid.blk_num)
        self.current_slot_index = rid.slot_num

    # operate on the current_slot_index
    def getInt(self, field_name):
        return self.rp.getInt(self.current_slot_index, field_name)

    def getString(self, field_name):
        return self.rp.getString(self.current_slot_index, field_name)

    def setInt(self, field_name, field_value):
        self.rp.setInt(self.current_slot_index, field_name, field_value)

    def setString(self, field_name, field_value):
        self.rp.setString(self.current_slot_index, field_name, field_value)

    def getRecordID(self):
        return RecordID(self.rp.blk.block_number, self.current_slot_index)

    def closeRecordPage(self):
        if self.rp:
            self.tx.unpin(self.rp.blk)


# scheme contains field name, type and byte length
# layout contains offset, additional padding
# record page uses layout into to write data to a block
# table scan uses record page to cycle through blocks in a file
# table manager uses table scan to bind table and layout in db metadata
class TableMgr:
    """Create and maintain table_catalog and field_catalog for each new table"""
    # Not using; but leaving as a reminder that all string can be of max 16 char long in the catalog tables
    # I am using 20 because some column lengths are long, such as len('field_byte_length') = 17
    max_name_length = 16

    def __init__(self, tx, db_init):
        self.tx = tx

        self.table_catalog_schema = Schema(
            ['table_name', 'str', 20],
            ['slot_size', 'int', 4]
        )
        self.table_catalog_layout = Layout(self.table_catalog_schema) # Used in getLayoutMetadata

        self.field_catalog_schema = Schema(
            ['table_name', 'str', 20],
            ['field_name', 'str', 20],
            ['field_type', 'str', 4],
            ['field_byte_length', 'int', 4],
            ['field_byte_offset', 'int', 4]
        )
        self.field_catalog_layout = Layout(self.field_catalog_schema) # Used in getLayoutMetadata

        # during db initialization we need to initialize these table that holds the table metadata
        if db_init:
            self.createTableMetadata(self.tx, 'table_catalog', self.table_catalog_schema)
            self.createTableMetadata(self.tx, 'field_catalog', self.field_catalog_schema)

    # Open the table_catalog, and field_catalog table
    #   and append new_table metadata to those tables
    def createTableMetadata(self, tx, new_table_name, new_sch):
        temp_layout = Layout(new_sch)

        # Add new table name and its slot size to the table_catalog table
        table_ts = TableScan(tx, 'table_catalog', self.table_catalog_layout)
        table_ts.nextEmptyRecord()
        table_ts.setString('table_name', new_table_name)
        table_ts.setInt('slot_size', temp_layout.slot_size)
        table_ts.closeRecordPage()

        # Add fields info of the new tables to field_catalog table
        field_ts = TableScan(tx, 'field_catalog', self.field_catalog_layout)
        for f in temp_layout.schema.getFields():
            field_ts.nextEmptyRecord()
            field_ts.setString('table_name', new_table_name)
            field_ts.setString('field_name', f)
            field_ts.setString('field_type', temp_layout.schema.field_info[f]['field_type'])
            field_ts.setInt('field_byte_length', temp_layout.schema.field_info[f]['field_byte_length'])
            field_ts.setInt('field_byte_offset', temp_layout.offset[f])
        field_ts.closeRecordPage()

    def getLayout(self, tx, table_name):
        # read table_catalog and field_catalog to generate the layout for a requested table
        ts = TableScan(tx, 'field_catalog', self.field_catalog_layout)
        temp_sch = Schema()
        while ts.nextRecord():
            if ts.getString('table_name') == table_name:
                temp_sch.addField(ts.getString('field_name'), ts.getString('field_type'), ts.getInt('field_byte_length'))
        return Layout(temp_sch)


# Fig 7.2 TableMgrTest: Using the TableMgr methods
fm: FileMgr = FileMgr('SimpleDB', 400, 8)
lm: LogMgr = LogMgr(fm, 'tst_log')
bm: BufferMgr = BufferMgr(fm, lm, 2)

tx: Transaction = Transaction(fm, lm, bm)
sch = Schema(['A', 'int', 4], ['B', 'str', 9])
tm: TableMgr = TableMgr(tx, True) # Create two tables; 1. table info, 2. field info for all table
tm.createTableMetadata(tx, 'MyTable', sch)
ly = tm.getLayout(tx, 'MyTable')
print(ly)
tx.commit()

# exit()
temp_tx = Transaction(fm, lm, bm)
temp_tm = TableMgr(temp_tx, False)
temp_ts = TableScan(temp_tx, 'table_catalog', temp_tm.table_catalog_layout)
print("table catalog content ::")
while temp_ts.nextRecord():
    print(
        temp_ts.getString('table_name'),
        temp_ts.getInt('slot_size')
    )
print('field catalog content :: ')
temp_ts = TableScan(temp_tx, 'field_catalog', temp_tm.field_catalog_layout)
while temp_ts.nextRecord():
    print(
        temp_ts.getString('table_name'),
        temp_ts.getString('field_name'),
        temp_ts.getString('field_type'),
        temp_ts.getInt('field_byte_length'),
        temp_ts.getInt('field_byte_offset')
    )
temp_tx.commit()
exit()


# Fig 6.18 TableScanTest
fm: FileMgr = FileMgr('SimpleDB', 400, 8)
lm: LogMgr = LogMgr(fm, 'tst_log')
bm: BufferMgr = BufferMgr(fm, lm, 2)
tx: Transaction = Transaction(fm, lm, bm)
sch: Schema = Schema()
sch.addField('A', 'int', 4)
sch.addField('B', 'str', 9)
layout: Layout = Layout(sch)

ts: TableScan = TableScan(tx, "T", layout)
print('Insertion')
rand_val = [49, 34, 40, 30, 1, 17, 18, 45, 27, 5, 7, 27, 43, 9, 31, 21, 2, 2, 28, 16, 44, 3, 14, 44, 47, 41, 22, 0, 23, 42, 3, 25, 3, 50, 29, 35, 28, 45, 50, 6, 49, 30, 18, 16, 42, 6, 8, 45, 11, 31]
rand_count = 0
ts.firstRecord()
for i in range(50):
    ts.nextEmptyRecord()
    # temp_val = random.randint(0, 50)
    temp_val = rand_val[rand_count]
    rand_count += 1
    ts.setInt('A', temp_val)
    ts.setString('B', 'rec' + str(temp_val))
    print('inserting ' + str(ts.getRecordID()) + '; ' + str(temp_val) + ' rec' + str(temp_val))

print('Deletion')
count = 0
ts.firstRecord()
while ts.nextRecord():
    a = ts.getInt('A')
    b = ts.getString('B')
    if a < 25:
        count += 1
        print('Deleting ' + str(ts.getRecordID()) + ' ; value ' + str(a) + ' ' + b)
        ts.deleteRecord()

print('Retained')
ts.firstRecord()
while ts.nextRecord():
    a = ts.getInt('A')
    b = ts.getString('B')
    print('Retained ' + str(ts.getRecordID()) + ' ; value ' + str(a) + ' ' + b)

ts.closeRecordPage()
tx.commit()

for l in lm.iterator():
    print(LogRecord.toString(l))

exit()

# Fig 6.15 RecordTest; Testing RecordPage, Schema, Layout
fm: FileMgr = FileMgr('SimpleDB', 400, 8)
lm: LogMgr = LogMgr(fm, 'tst_log')
bm: BufferMgr = BufferMgr(fm, lm, 8)

tx: Transaction = Transaction(fm, lm, bm)
blk: Block = tx.append('testfile')
tx.pin(blk)
sch: Schema = Schema()
sch.addField('A', 'int', 4)
sch.addField('B', 'str', 9)
layout: Layout = Layout(sch)
rp: RecordPage = RecordPage(tx, blk, layout)
rp.format()

print("RecordPage init")
rand_val = [49, 34, 40, 30, 1, 17, 18, 45, 27, 5, 7, 27, 43, 9, 31, 21, 2, 2, 28, 16, 44, 3, 14, 44, 47, 41, 22, 0, 23, 42, 3, 25, 3, 50, 29, 35, 28, 45, 50, 6, 49, 30, 18, 16, 42, 6, 8, 45, 11, 31]
rand_count = 0
next_empty_slot = rp.insertAfter(-1)
while next_empty_slot >= 0:
    rec_val = rand_val[rand_count]
    rand_count += 1
    rp.setInt(next_empty_slot, 'A', rec_val)
    rp.setString(next_empty_slot, 'B', 'rec' + str(rec_val))
    print('Insert slot ' + str(next_empty_slot) + ' [' + str(rec_val) + ', rec' + str(rec_val) + ']')
    next_empty_slot = rp.insertAfter(next_empty_slot)

print("RecordPage deletion")
next_used_slot = rp.nextAfter(-1)
del_counter = 0
while next_used_slot >= 0:
    a = rp.getInt(next_used_slot, 'A')
    b = rp.getString(next_used_slot, 'B')
    if rp.getInt(next_used_slot, 'A') < 25:
        del_counter += 1
        rp.delete(next_used_slot)
        print('Deleting slot ' + str(next_used_slot) + ' [' + str(a) + ',' + str(b) + ']')
    next_used_slot = rp.nextAfter(next_used_slot)

print("RecordPage Retained")
next_empty_slot = rp.nextAfter(-1)
while next_empty_slot >= 0:
    a = rp.getInt(next_empty_slot, 'A')
    b = rp.getString(next_empty_slot, 'B')
    print('Retained slot ' + str(next_empty_slot) + ' [' + str(a) + ',' + str(b) + ']')
    next_empty_slot = rp.nextAfter(next_empty_slot)

tx.unpin(blk) # Not necessary as commit() unpins all pinned buffers
tx.commit()

# for l in lm.iterator():
#     print(LogRecord.toString(l))


exit()

sch = Schema()
sch.addField('cid', 'int', 4)
sch.addField('title', 'str', 20)
sch.addField('deptid', 'int', 4)

layout = Layout(sch)
for k, v in layout.schema.field_info.items():
    print(k, v['field_byte_length'], layout.offset[k])
print(layout.slot_size)

exit()


# RecoveryTest - Not mentioned the book
fm: FileMgr = FileMgr('SimpleDB', 400, 8)
lm: LogMgr = LogMgr(fm, 'tst_log')
bm: BufferMgr = BufferMgr(fm, lm, 2)

if fm.length('testfile'):
    # recover
    tx = Transaction(fm, lm, bm)
    tx.recover()
    print('recovery - complete')
else:
    # init
    tx1 = Transaction(fm, lm, bm)
    tx2 = Transaction(fm, lm, bm)
    blk0 = Block('testfile', 0)
    blk1 = Block('testfile', 1)
    tx1.pin(blk0)
    tx2.pin(blk1)
    pos = 0
    for i in range(6):
        tx1.setInt(blk0, pos, pos, False)
        tx2.setInt(blk1, pos, pos, False)
        pos += 4
    tx1.setString(blk0, 30, "abc", False)
    tx2.setString(blk1, 30, "def", False)
    tx1.commit()
    tx2.commit()

    # modify
    tx3 = Transaction(fm, lm, bm)
    tx4 = Transaction(fm, lm, bm)
    tx3.pin(blk0)
    tx4.pin(blk1)
    pos = 0
    for i in range(6):
        tx3.setInt(blk0, pos, pos+100, True)
        tx4.setInt(blk1, pos, pos+100, True)
        pos += 4
    tx3.setString(blk0, 30, 'uvw', True)
    tx4.setString(blk1, 30, 'xyz', True)
    # tx3.commit()
    # tx4.commit()
    bm.flushAll(3)
    bm.flushAll(4)

    tx3.rollback()

    print('init and modify - complete')
    for l in lm.iterator():
        print(LogRecord.toString(l))
exit()

# Fig 5.19 ConcurrencyTest; Testing Concurrency class
fm = FileMgr('SimpleDB', 400, 8)
lm = LogMgr(fm, 'tst_log')
bm = BufferMgr(fm, lm, 8)

def A():
    try:
        txA = Transaction(fm, lm, bm)
        blk1 = Block('testfile', 1)
        blk2 = Block('testfile', 2)
        txA.pin(blk1)
        txA.pin(blk2)
        print('txA requesting slock1')
        txA.getInt(blk1, 0)
        print('txA received slock1')
        time.sleep(1)
        print('txA requesting slock2')
        txA.getInt(blk2, 0)
        print('txA received slock2')
        txA.commit()
    except Exception as e:
        txA.rollback()
        print("Exception: " + str(e))

def B():
    try:
        txB = Transaction(fm, lm, bm)
        blk1 = Block('testfile', 1)
        blk2 = Block('testfile', 2)
        txB.pin(blk1)
        txB.pin(blk2)
        print('txB requesting xlock2')
        txB.setInt(blk2, 0, 0, False)
        print('txB received xlock2')
        time.sleep(1)
        print('txB requesting slock1')
        txB.getInt(blk1, 0)
        print('txB received slock1')
        txB.commit()
    except Exception as e:
        txB.rollback()
        print("Exception: " + str(e))

def C():
    try:
        txC = Transaction(fm, lm, bm)
        blk1 = Block('testfile', 1)
        blk2 = Block('testfile', 2)
        txC.pin(blk1)
        txC.pin(blk2)
        print('txC requesting xlock1')
        txC.setInt(blk1, 0, 0, False)
        print('txC received xlock1')
        time.sleep(1)
        print('txC requesting slock2')
        txC.getInt(blk2, 0)
        print('txC received slock2')
        txC.commit()
    except Exception as e:
        txC.rollback()
        print("Exception: " + str(e))

t1 = threading.Thread(target=A)
t1.start()
t2 = threading.Thread(target=B)
t2.start()
t3 = threading.Thread(target=C)
t3.start()

t1.join()
t2.join()
t3.join()

for l in lm.iterator():
    print(LogRecord.toString(l))

exit()

# Fig 5.3 TxTest; Testing Transactions
fm = FileMgr('SimpleDB', 400, 8)
lm = LogMgr(fm, 'tst_log')
bm = BufferMgr(fm, lm, 8)

tx1 = Transaction(fm, lm, bm)
blk = Block('testfile', 1)
tx1.pin(blk)
tx1.setInt(blk, 80, 1, False)
tx1.setString(blk, 40, "one", False)
tx1.commit()

tx2 = Transaction(fm, lm, bm)
tx2.pin(blk)
ival = tx2.getInt(blk, 80)
sval = tx2.getString(blk, 40)
print("Initial value at loc 80 =", str(ival))
print("Initial value at loc 40 =", str(sval))
newival = ival + 1
newsval = sval + '!'
tx2.setInt(blk, 80, newival, True)
tx2.setString(blk, 40, newsval, True)
tx2.commit()

tx3 = Transaction(fm, lm, bm)
tx3.pin(blk)
print('new value at loc 80 = ', str(tx3.getInt(blk, 80)))
print('new value at loc 40 = ', str(tx3.getString(blk, 40)))
tx3.setInt(blk, 80, 9999, True)
print('pre-rollback value at loc 80 = ', str(tx3.getInt(blk, 80)))
tx3.rollback()

tx4 = Transaction(fm, lm, bm)
tx4.pin(blk)
print('post-rollback value at loc 80 = ', str(tx4.getInt(blk, 80)))
tx4.commit()

exit()

# Fig 4.12 Testing Buffer Manager
fm : FileMgr = FileMgr('simpledb', 400, 8)
lm : LogMgr = LogMgr(fm, 'tst_log')
bm : BufferMgr = BufferMgr(fm, lm, 3)
buff = [] # we will append six BLock references in this list
buff.append(bm.pin(Block('testfile', 0)))
buff.append(bm.pin(Block('testfile', 1)))
buff.append(bm.pin(Block('testfile', 2)))
bm.unpin(buff[1]) # unpin testfile, 1
buff[1] = None
buff.append(bm.pin(Block('testfile', 0))) # no effect
buff.append(bm.pin(Block('testfile', 1))) # pin testfile, 1 again
print('Available buffer count: ' + str(bm.pool_availability))
try:
    print("Attempting to pin block 3...")
    buff.append(bm.pin(Block('testfile', 3)))
except Exception as e:
    print("Exception: " + str(e))
bm.unpin(buff[2]) # unpin testfile, 2
buff[2] = None
buff.append(bm.pin(Block('testfile', 3))) # pin testfile, 3

print("Final buffer allocation.")
for i in range(len(buff)):
    if buff[i]:
        print('buff[' + str(i) + '] pinned to block ' + str(buff[i].block))

exit()


# Fig 4.11 Testing Buffer
fm = FileMgr('simpledb', 400, 8)
lm = LogMgr(fm, 'tst_log')
bm = BufferMgr(fm, lm, 3)
buff1 = bm.pin(Block('testfile', 1))
n = buff1.page.getInt(80) # it should return empty because testfile is of size zero
buff1.page.setData(80, n + 1)
buff1.setModified(1, 0) # does lsn start at zero?
print('The new value is ', n+1)
bm.unpin(buff1) # we do not immediately write it back to disk because some other client might pin it again

buff2 = bm.pin(Block('testfile', 2)) # this write the block 1 back to disk
buff3 = bm.pin(Block('testfile', 3))
buff4 = bm.pin(Block('testfile', 4))

bm.unpin(buff2)
buff11 = bm.pin(Block('testfile', 1))
buff11.page.setData(80, 9999)
buff11.setModified(1, 0)
buff11.unpin() # This modification won't get written to disk because there is noting forcing it
bm.flushAll(2)

exit()

# Fig 4.5 Testing Log Manager
fm = FileMgr('simpledb', 400, 8) # Kernel page size; usually 4096 bytes
lm = LogMgr(fm, 'tst_log')

def createLogRecord(s,i):
    temp_bytearray = bytearray(4 + len(s) + 4) # length of string + string + one number
    temp_page = Page(temp_bytearray) # creating page with desired size because
    pos = temp_page.setData(0, s)
    temp_page.setData(pos, i)
    lsn = lm.appendLog(temp_page.bb)
    return lsn


for i in range(1, 36):
    lsn = createLogRecord('record' + str(i), i + 100)
    print('Adding ' + '(lsn: ' + str(lsn) + '): \t' + 'record' + str(i) + str(i + 100))

for l in lm.iterator():
    temp_page = Page(l) # We have keep it in memory to parse its content
    record_str = temp_page.getStr(0)
    record_int = temp_page.getInt(4 + len(record_str)) # also need to add 4 byte for the recoded length of the string
    print('Reading:  ' + record_str + str(record_int))

for i in range(36, 71):
    lsn = createLogRecord('record' + str(i), i + 100)
    print('Adding ' + '(lsn: ' + str(lsn) + '): \t' + 'record' + str(i) + str(i + 100))

for l in lm.iterator():
    temp_page = Page(l) # We have keep it in memory to parse its content
    record_str = temp_page.getStr(0)
    record_int = temp_page.getInt(4 + len(record_str)) # also need to add 4 byte for the recoded length of the string
    print('Reading:  ' + record_str + str(record_int))

exit()

# 3.12 Testing file manager
# File for each table; many blocks(identified by id) for each file
# these files needs to be created inside a folded named $db
fm = FileMgr('simpledb', 400, 8) # Kernel page size; usually 4096 bytes
b1 = Block('testfile', 2)
p1 = Page(fm.block_size)
pos = 88 # position relative to the current block, so should always be between 0 <= block_size < 400
new_pos = pos + p1.setData(pos, 'abcdefghijklm')
p1.setData(new_pos, 345)
fm.writePageToBlock(b1, p1) # won't work because r+b is expecting the file to exists; in LogTest we are creating the empty file in appendEmptyBlock

temp_page = Page(fm.block_size)
fm.readBlockToPage(b1, temp_page)
print(temp_page.getStr(pos))
print(temp_page.getInt(new_pos))