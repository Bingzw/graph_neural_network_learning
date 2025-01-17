import torch
import torch.nn as nn
import torch.nn.functional as F


class GATLayer(nn.Module):
    def __init__(self, c_in, c_out, num_heads=1, concat_heads=True, alpha=0.2):
        super(GATLayer, self).__init__()
        self.num_heads = num_heads
        self.concat_heads = concat_heads
        if self.concat_heads:
            assert c_out % num_heads == 0  # make sure c_out can be divided by num_heads
            c_out = c_out // num_heads

        self.projection = nn.Linear(c_in, c_out * num_heads, bias=False)
        self.a = nn.Parameter(torch.Tensor(num_heads, 2 * c_out))  # one per head
        self.leakyrelu = nn.LeakyReLU(alpha)

        # initialize weights
        nn.init.xavier_normal_(self.projection.weight.data, gain=1.414)
        nn.init.xavier_normal_(self.a.data, gain=1.414)

    def forward(self, node_feats, adj_matrix, print_attn_probs=False):
        """
        :param node_feats: Input features of the node. Shape: [batch_size, c_in]
        :param adj_matrix: Adjacency matrix including self-connections. Shape: [batch_size, num_nodes, num_nodes]
        :param print_attn_probs: If True, the attention weights are printed during the forward pass (for debugging purposes)
        :return:
        """
        batch_size, num_nodes = node_feats.size(0), node_feats.size(1)
        # Apply linear layer and sort nodes by head
        node_feats = self.projection(node_feats).view(batch_size, num_nodes, self.num_heads, -1)
        # We need to calculate the attention logits for every edge in the adjacency matrix
        # Doing this on all possible combinations of nodes is very expensive
        # => Create a tensor of [W*h_i||W*h_j] with i and j being the indices of all edges
        edges = adj_matrix.nonzero(as_tuple=False)
        node_feats_flat = node_feats.view(batch_size*num_nodes, self.num_heads, -1)
        edge_indices_row = edges[:, 0] * num_nodes + edges[:, 1]
        edge_indices_col = edges[:, 0] * num_nodes + edges[:, 2]
        a_input = torch.cat([
            torch.index_select(input=node_feats_flat, index=edge_indices_row, dim=0),
            torch.index_select(input=node_feats_flat, index=edge_indices_col, dim=0)
        ], dim=-1)
        # Calculate attention MLP output (independent for each head)
        attn_logits = torch.einsum('bhc,hc->bh', a_input, self.a)
        attn_logits = self.leakyrelu(attn_logits)

        # Map list of attention values back into a matrix
        attn_matrix = attn_logits.new_zeros(adj_matrix.shape + (self.num_heads,)).fill_(-9e15)
        attn_matrix[adj_matrix[..., None].repeat(1, 1, 1, self.num_heads) == 1] = attn_logits.reshape(-1)

        # Weighted average of attention
        attn_probs = F.softmax(attn_matrix, dim=2)
        if print_attn_probs:
            print("Attention probs\n", attn_probs.permute(0, 3, 1, 2))
        node_feats = torch.einsum('bijh,bjhc->bihc', attn_probs, node_feats)

        # If heads should be concatenated, we can do this by reshaping. Otherwise, take mean
        if self.concat_heads:
            node_feats = node_feats.reshape(batch_size, num_nodes, -1)
        else:
            node_feats = node_feats.mean(dim=2)

        return node_feats
