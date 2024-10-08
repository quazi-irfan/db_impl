### pySimpleDB

This is a python implementation of [SimpleDB](https://cs.bc.edu/~sciore/simpledb/), originally authored by [Edward Sciore](https://www.bc.edu/bc-web/schools/morrissey/departments/computer-science/people/faculty-directory/edward-sciore.html)[1, 2].

### Features
- ACID Compliance
    - Atomicity and Durability is implemented using Logging and Recovery manager
    - Consistency is implemented using runtime check in the query planner
    - Isolation is implemented by Serializing transactions
- Storage and Buffer Pool
  - Files are treated as raw disk and part of file(blocks) are paged into memory
  - Buffer pool maintins a fixed number of in-memory blocks(pages) to minimize disk seek
- Concurrent Transactions Support
    - Supports multiple user simultaneously using concurrent transactions
    - Serializability of concurrent transactions is enforced using a variant of two phase locking
    - In this varaient, locks(shared and exclusive) are acquired on demand, and all released at transcation completion
    - Locks are acquired on blocks
- Recovery Manager
  - Write ahead log for recovery
  - Recovery manager peforms undo operation on all uncommited transactions during database startup
  - Log files gets very large, but recovery manager only reads until a quiescent checkpoint
- Log Manager
  - Each modication to a field generates a log entry capturing the prior value of the field
  - This prior value is used by Recovery manager to undo all uncommited transactions
- SQL Support
  - 4 bit integer and fixed length string
  - Supported Relational operators: Project, Product, Select
  - Select statement with Where clause with multiple predicate(equality operator only)
  - Simplified form of Create, Update and Delete statement

### References
- [1] Book [Database Design and Implementation by Edward Sciore](https://link.springer.com/book/10.1007/978-3-030-33836-7)
- [2] Article [SimpleDB: a simple java-based multiuser syst for teaching database internals](https://dl.acm.org/doi/abs/10.1145/1227504.1227498)

